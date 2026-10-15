import asyncio
import json
import sys
from threading import Event

import pytest

from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.events import EventKind
from minicodex.backend.core.models import (
    ModelResponse,
    ThreadState,
    ToolCall,
    TurnState,
    TurnStatus,
)
from minicodex.backend.core.runtime import AgentRuntime
from minicodex.backend.execution.approval import ApprovalManager
from minicodex.backend.llm.fake import FakeLLMProvider


@pytest.mark.parametrize("decision", ["approve", "reject", "conflict", "limit"])
async def test_metrics_count_applied_unique_paths_including_add_delete(tmp_path, decision):
    (tmp_path / "a").write_bytes(b"old\n")
    (tmp_path / "b").write_bytes(b"bye\n")
    first = "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n"
    second = (
        "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-new\n+newer\n"
        "--- a/b\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye\n"
        "--- /dev/null\n+++ b/c\n@@ -0,0 +1 @@\n+created\n"
    )
    provider = FakeLLMProvider(
        [
            ModelResponse(tool_calls=(ToolCall("one", "apply_patch", {"patch": first}),)),
            ModelResponse(tool_calls=(ToolCall("two", "apply_patch", {"patch": second}),)),
            ModelResponse(text="done"),
        ],
        delay_s=0,
    )
    approvals = ApprovalManager()
    thread = ThreadState(tmp_path)
    turn = TurnState(thread.thread_id)
    requests = []

    async def emit(event):
        if event.kind == EventKind.APPROVAL_REQUESTED:
            request = json.loads(event.text)
            requests.append(request)
            await asyncio.sleep(0.03)
            if decision == "conflict" and len(requests) == 2:
                (tmp_path / "a").write_bytes(b"external change\n")
            await approvals.respond(request["approval_id"], turn.turn_id, decision != "reject")

    await AgentRuntime(
        provider,
        settings=ModelSettings(execution_mode="write"),
        approvals=approvals,
        max_steps=2 if decision == "limit" else 20,
    ).run_turn(thread, turn, "modify", Event(), emit)
    expected = (
        set() if decision == "reject" else {"a"} if decision == "conflict" else {"a", "b", "c"}
    )
    assert turn.edited_files == expected
    assert turn.duration_s >= 0.03 * len(requests)
    assert turn.status == (
        TurnStatus.LIMIT_EXCEEDED if decision == "limit" else TurnStatus.COMPLETED
    )
    assert thread.turns == [turn] and turn.events[-1].kind.value.startswith("turn.")
    if decision in {"approve", "limit"}:
        assert (tmp_path / "a").read_bytes() == b"newer\n"
        assert not (tmp_path / "b").exists() and (tmp_path / "c").exists()


@pytest.mark.parametrize("outcome", ["success", "failed", "cancelled", "incomplete"])
async def test_native_metrics_keep_edits_after_nonzero_exit_or_cancellation(tmp_path, outcome):
    (tmp_path / "a").write_bytes(b"old\n")
    (tmp_path / "b").write_bytes(b"bye\n")
    source = (
        "from pathlib import Path;import time,sys;"
        "Path('a').write_bytes(b'newer\\n');Path('b').unlink();"
        "Path('c').write_bytes(b'created\\n');print('made edits',flush=True);"
    )
    source += {
        "success": "sys.exit(0)",
        "failed": "sys.exit(7)",
        "cancelled": "time.sleep(30)",
        "incomplete": "Path('big').write_bytes(b'x'*(1024*1024+1))",
    }[outcome]
    provider = FakeLLMProvider(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        "patch",
                        "apply_patch",
                        {
                            "patch": "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n",
                        },
                    ),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        "command",
                        "run_command",
                        {
                            "argv": [sys.executable, "-c", source],
                        },
                    ),
                )
            ),
            ModelResponse(text="done"),
        ],
        delay_s=0,
    )
    approvals = ApprovalManager()
    cancel = Event()
    thread = ThreadState(tmp_path)
    turn = TurnState(thread.thread_id)

    async def emit(event):
        if event.kind == EventKind.APPROVAL_REQUESTED:
            request = json.loads(event.text)
            await approvals.respond(request["approval_id"], turn.turn_id, True)
        if outcome == "cancelled" and event.kind == EventKind.COMMAND_OUTPUT:
            cancel.set()

    await AgentRuntime(
        provider,
        settings=ModelSettings(execution_mode="native"),
        approvals=approvals,
    ).run_turn(thread, turn, "modify and test", cancel, emit)
    assert turn.edited_files == ({"a"} if outcome == "incomplete" else {"a", "b", "c"})
    assert turn.file_metrics_complete == (outcome != "incomplete")
    assert turn.status == (TurnStatus.CANCELLED if outcome == "cancelled" else TurnStatus.COMPLETED)
    receipt = next(m for m in thread.history if m.tool_call_id == "command")
    observation = json.loads(receipt.content)
    assert observation["success"] == (outcome in {"success", "incomplete"})
    assert json.loads(observation["result"])["cleanup_verified"]
