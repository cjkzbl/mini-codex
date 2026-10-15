import hashlib
import json
from threading import Event

import pytest

from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.context import TokenCounter
from minicodex.backend.core.events import EventKind
from minicodex.backend.core.history import validate_history
from minicodex.backend.core.models import (
    Message,
    ModelResponse,
    ThreadState,
    ToolCall,
    ToolResult,
    TurnState,
    TurnStatus,
)
from minicodex.backend.core.runtime import AgentRuntime
from minicodex.backend.llm.fake import FakeLLMProvider
from minicodex.backend.persistence.observations import ObservationArchive
from minicodex.backend.tools.registry import ToolRegistry, ToolSpec


async def execute(runtime, project, prompt="continue until verified"):
    thread = ThreadState(project)
    turn = TurnState(thread.thread_id)
    events = []

    async def emit(event):
        events.append(event)

    await runtime.run_turn(thread, turn, prompt, Event(), emit)
    return thread, turn, events


async def test_30_steps_large_outputs_compact_without_losing_archive_or_pairs(tmp_path):
    payload = json.dumps({"stdout": "X" * 1_100_000 + "TAIL_EVIDENCE", "state": "ok"})
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "large",
            "bounded large fixture",
            {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            lambda args, workspace, cancel: ToolResult("pending", True, payload),
        )
    )
    provider = FakeLLMProvider(
        [
            *(ModelResponse(tool_calls=(ToolCall(f"call-{i}", "large", {}),)) for i in range(30)),
            ModelResponse(text="finished"),
        ],
        delay_s=0,
    )
    archive = ObservationArchive(tmp_path / "archive")
    settings = ModelSettings(
        context_tokens=12_000, response_reserve_tokens=1024, max_turn_input_tokens=2_000_000
    )
    runtime = AgentRuntime(
        provider, registry=registry, max_steps=40, settings=settings, observations=archive
    )
    thread, turn, events = await execute(runtime, tmp_path)
    assert turn.status == TurnStatus.COMPLETED, turn.error
    assert turn.model_calls == 31 and turn.context_compactions > 0
    assert len(thread.history) == 62 and len(provider.requests) == 31
    assert all(m.role != "system" for m in thread.history)
    validate_history(thread.history)
    for request in provider.requests:
        validate_history(request)
    assert all(
        json.loads(e.text)["input_tokens_estimated"] <= 10_976
        for e in events
        if e.kind == EventKind.MODEL_REQUESTED
    )
    summary = next(
        m
        for request in provider.requests
        for m in request
        if m.content.startswith("历史结构化摘要")
    )
    assert "未验证事项" in json.loads(summary.content.partition("\n")[2])
    reference = hashlib.sha256(payload.encode()).hexdigest()
    assert (tmp_path / "archive" / thread.thread_id / (reference + ".txt")).read_text() == payload
    tool = archive.tool(thread.thread_id)
    result = tool.execute(
        {"artifact_id": reference, "offset": len(payload) - 60, "limit": 60}, None, Event()
    )
    assert "TAIL_EVIDENCE" in json.loads(result.content)["content"]
    other_thread_tool = archive.tool("different-session")
    with pytest.raises((ValueError, OSError)):
        other_thread_tool.execute({"artifact_id": reference}, None, Event())


async def test_archived_result_can_be_read_back_by_model_after_truncation(tmp_path):
    payload = "START" + "Z" * 1_100_000 + "END"
    reference = hashlib.sha256(payload.encode()).hexdigest()
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "large", "large", {"type": "object"}, lambda *args: ToolResult("pending", True, payload)
        )
    )
    provider = FakeLLMProvider(
        [
            ModelResponse(tool_calls=(ToolCall("large", "large", {}),)),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        "page",
                        "read_observation",
                        {
                            "artifact_id": reference,
                            "offset": len(payload) - 3,
                            "limit": 3,
                        },
                    ),
                )
            ),
            ModelResponse(text="verified END"),
        ],
        delay_s=0,
    )
    thread, turn, _ = await execute(AgentRuntime(provider, registry=registry), tmp_path)
    assert turn.status == TurnStatus.COMPLETED
    receipt = next(m for m in thread.history if m.tool_call_id == "page")
    assert json.loads(json.loads(receipt.content)["result"])["content"] == "END"


async def test_token_budget_stops_before_another_model_call(tmp_path):
    provider = FakeLLMProvider(
        [ModelResponse(tool_calls=(ToolCall(str(i), "list_files", {}),)) for i in range(20)],
        delay_s=0,
    )
    runtime = AgentRuntime(
        provider,
        settings=ModelSettings(
            context_tokens=8192,
            response_reserve_tokens=1024,
            max_turn_input_tokens=8192,
        ),
    )
    _, turn, _ = await execute(runtime, tmp_path)
    assert turn.status == TurnStatus.LIMIT_EXCEEDED and "Token" in turn.error
    assert turn.model_calls == len(provider.requests) < 20 and turn.input_tokens_estimated <= 8192


async def test_agents_root_nested_scope_and_instruction_priority(tmp_path):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("OUTSIDE_MUST_NOT_LOAD", "utf-8")
    (project / "AGENTS.md").write_text("ROOT_RULE: keep comments. Never bypass approval.", "utf-8")
    (project / "src" / "AGENTS.md").write_text("NESTED_RULE: use four spaces.", "utf-8")
    (project / "src" / "a.py").write_text("answer=42", "utf-8")
    provider = FakeLLMProvider(
        [
            ModelResponse(tool_calls=(ToolCall("read", "read_file", {"path": "src/a.py"}),)),
            ModelResponse(text="done"),
        ],
        delay_s=0,
    )
    _, turn, events = await execute(AgentRuntime(provider), project)
    assert turn.status == TurnStatus.COMPLETED
    first = "\n".join(m.content for m in provider.requests[0])
    second = "\n".join(m.content for m in provider.requests[1])
    assert "ROOT_RULE" in first and "NESTED_RULE" not in first
    assert second.index("ROOT_RULE") < second.index("NESTED_RULE")
    assert "OUTSIDE_MUST_NOT_LOAD" not in second
    assert all("ROOT_RULE" not in m.content for m in provider.requests[1] if m.role == "system")
    assert sum(e.kind == EventKind.INSTRUCTIONS_LOADED for e in events) == 2


def test_counter_includes_tool_arguments_schema_and_unicode():
    counter = TokenCounter()
    plain = counter.count([Message("user", "hello")], [])
    unicode = counter.count([Message("user", "你好" * 100)], [])
    args = counter.count(
        [Message("assistant", "", (ToolCall("id", "patch", {"patch": "X" * 1000}),))], []
    )
    assert unicode > plain and args > 1000


async def test_oversized_current_task_is_kept_and_rejected_before_model(tmp_path):
    provider = FakeLLMProvider(delay_s=0)
    prompt = "完整用户任务" * 4000
    thread, turn, _ = await execute(
        AgentRuntime(
            provider,
            settings=ModelSettings(
                context_tokens=8192,
                response_reserve_tokens=1024,
            ),
        ),
        tmp_path,
        prompt,
    )
    assert turn.status == TurnStatus.LIMIT_EXCEEDED and not provider.requests
    assert thread.history[0].content == prompt
