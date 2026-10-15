import asyncio
import json
from collections.abc import AsyncIterator
from threading import Event

import pytest

from minicodex.backend.core.events import EventKind
from minicodex.backend.core.history import validate_history
from minicodex.backend.core.models import (
    Message,
    ModelResponse,
    ThreadState,
    ToolCall,
    TurnState,
    TurnStatus,
)
from minicodex.backend.core.runtime import AgentRuntime
from minicodex.backend.llm.fake import FakeLLMProvider


async def run(tmp_path, responses, **options):
    thread = ThreadState(tmp_path)
    turn = TurnState(thread.thread_id)
    provider = FakeLLMProvider(responses, delay_s=0)
    events = []

    async def emit(event):
        events.append(event)

    await AgentRuntime(provider, **options).run_turn(thread, turn, "explain", Event(), emit)
    return thread, turn, provider, events


async def test_multi_tool_loop_uses_real_observations_and_preserves_order(tmp_path):
    (tmp_path / "中文.py").write_text("def answer():\n    return 42\n", "utf-8")
    calls = (
        ToolCall("list-1", "list_files", {}),
        ToolCall("read-2", "read_file", {"path": "中文.py"}),
        ToolCall("search-3", "search_code", {"pattern": "return"}),
    )
    thread, turn, provider, events = await run(
        tmp_path, [ModelResponse(tool_calls=calls), ModelResponse(text="answer returns 42")]
    )
    assert turn.status == TurnStatus.COMPLETED
    assert turn.model_calls == 2 and turn.tool_calls == 3
    validate_history(thread.history)
    replies = [m for m in provider.requests[1] if m.role == "tool"]
    assert [m.tool_call_id for m in replies] == [c.call_id for c in calls]
    assert "42" in replies[1].content
    assert [e.call_id for e in events if e.kind == EventKind.TOOL_FINISHED] == [
        c.call_id for c in calls
    ]


@pytest.mark.parametrize(
    "call",
    [
        ToolCall("c", "shell", {"command": "anything"}),
        ToolCall("c", "read_file", {"path": "../secret"}),
        ToolCall("c", "list_files", {"max_results": -1}),
    ],
)
async def test_denied_and_bad_tools_return_failure_observation(tmp_path, call):
    thread, turn, provider, _ = await run(
        tmp_path, [ModelResponse(tool_calls=(call,)), ModelResponse(text="Tool was denied.")]
    )
    validate_history(thread.history)
    assert not json.loads(provider.requests[1][-1].content)["success"]
    assert turn.status == TurnStatus.COMPLETED  # response can explain a failed read


@pytest.mark.parametrize("options", [{"max_steps": 1}, {"max_tool_calls": 1}])
async def test_budget_exhaustion_is_not_completion_and_history_is_reusable(tmp_path, options):
    calls = (ToolCall("one", "list_files", {}), ToolCall("two", "list_files", {}))
    thread, turn, _, events = await run(tmp_path, [ModelResponse(tool_calls=calls)], **options)
    assert turn.status == TurnStatus.LIMIT_EXCEEDED
    assert events[-1].kind == EventKind.TURN_LIMIT_EXCEEDED
    validate_history(thread.history)
    assert len([m for m in thread.history if m.role == "tool"]) == 2


async def test_cancellation_after_assistant_calls_fills_unexecuted_receipts(tmp_path):
    cancel = Event()
    provider = FakeLLMProvider(
        [
            ModelResponse(
                tool_calls=(ToolCall("one", "list_files", {}), ToolCall("two", "list_files", {}))
            )
        ],
        delay_s=0,
    )
    thread = ThreadState(tmp_path)
    turn = TurnState(thread.thread_id)
    events = []

    async def emit(event):
        events.append(event)
        if event.kind == EventKind.TOOL_STARTED:
            cancel.set()

    await AgentRuntime(provider).run_turn(thread, turn, "read", cancel, emit)
    validate_history(thread.history)
    assert turn.status == TurnStatus.CANCELLED
    assert len([m for m in thread.history if m.role == "tool"]) == 2
    assert not any(e.kind == EventKind.TOOL_FINISHED for e in events)


async def test_turn_timeout_is_explicit(tmp_path):
    thread = ThreadState(tmp_path)
    turn = TurnState(thread.thread_id)
    events = []

    async def emit(event):
        events.append(event)

    await AgentRuntime(FakeLLMProvider(delay_s=10), max_turn_s=0.02).run_turn(
        thread, turn, "hello", Event(), emit
    )
    assert turn.status == TurnStatus.FAILED
    assert "超时" in turn.error


async def test_duplicate_call_ids_fail_before_execution(tmp_path):
    call = ToolCall("duplicate", "list_files", {})
    _, turn, _, events = await run(tmp_path, [ModelResponse(tool_calls=(call, call))])
    assert turn.status == TurnStatus.FAILED
    assert not any(e.kind == EventKind.TOOL_STARTED for e in events)


def test_history_rejects_dangling_or_orphaned_receipt():
    with pytest.raises(ValueError):
        validate_history([Message("assistant", "", (ToolCall("c", "list_files", {}),))])
    with pytest.raises(ValueError):
        validate_history([Message("tool", "ok", tool_call_id="c")])


async def test_two_turns_preserve_history_and_event_identity(tmp_path):
    provider = FakeLLMProvider(delay_s=0)
    runtime = AgentRuntime(provider)
    thread = ThreadState(tmp_path)
    for index in (1, 2):
        events = []

        async def emit(event, events=events):
            events.append(event)

        turn = TurnState(thread.thread_id)
        await runtime.run_turn(thread, turn, f"question {index}", Event(), emit)
        assert turn.status == TurnStatus.COMPLETED
        assert events[0].kind == EventKind.TURN_STARTED
        assert events[-1].kind == EventKind.TURN_COMPLETED
        assert [e.sequence for e in events] == list(range(1, len(events) + 1))
        assert {e.turn_id for e in events} == {turn.turn_id}
    assert [m.role for m in thread.history] == ["user", "assistant", "tool", "assistant"] * 2
    assert len(provider.requests[2]) == 6
    assert "第 2 轮" in thread.history[-1].content


@pytest.mark.parametrize("empty", [True, False])
async def test_empty_and_failed_provider_are_failed_turns(tmp_path, empty):
    class Broken(FakeLLMProvider):
        async def stream(
            self, messages: list[Message], tools
        ) -> AsyncIterator[str | ModelResponse]:
            if not empty:
                raise RuntimeError("offline failure")
            for item in ():
                yield item

    events = []

    async def emit(event):
        events.append(event)

    thread = ThreadState(tmp_path)
    turn = TurnState(thread.thread_id)
    await AgentRuntime(Broken()).run_turn(thread, turn, "hello", Event(), emit)
    assert turn.status == TurnStatus.FAILED
    assert events[-1].kind == EventKind.TURN_FAILED
    assert turn.error


async def test_cancellation_interrupts_waiting_provider(tmp_path):
    runtime = AgentRuntime(FakeLLMProvider(delay_s=10))
    thread = ThreadState(tmp_path)
    turn = TurnState(thread.thread_id)
    started = asyncio.Event()
    events = []

    async def emit(event):
        events.append(event)
        if event.kind == EventKind.TURN_STARTED:
            started.set()

    task = asyncio.create_task(runtime.run_turn(thread, turn, "hello", Event(), emit))
    await started.wait()
    task.cancel()
    await task
    assert turn.status == TurnStatus.CANCELLED
    assert events[-1].kind == EventKind.TURN_CANCELLED
    assert not any(e.kind == EventKind.TURN_COMPLETED for e in events)
