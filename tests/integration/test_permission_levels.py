import asyncio
import json
import sys
from threading import Event

import pytest

from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.events import EventKind
from minicodex.backend.core.models import ModelResponse, ThreadState, ToolCall, TurnState
from minicodex.backend.core.runtime import AgentRuntime
from minicodex.backend.execution.approval import ApprovalManager
from minicodex.backend.execution.workspace import Workspace
from minicodex.backend.llm.fake import FakeLLMProvider
from minicodex.backend.tools.coding import make_coding_registry
from minicodex.backend.tools.router import ToolRouter


@pytest.mark.parametrize("level", ["readonly", "default", "full"])
async def test_permission_tiers_control_real_patch_command_and_approval(tmp_path, level):
    (tmp_path / "a.py").write_bytes(b"answer = 1\n")
    provider = FakeLLMProvider(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        "patch",
                        "apply_patch",
                        {
                            "patch": "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n"
                            "-answer = 1\n+answer = 2\n"
                        },
                    ),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        "cmd",
                        "run_command",
                        {
                            "argv": [
                                sys.executable,
                                "-c",
                                "from a import answer;assert answer == 2;print('verified')",
                            ]
                        },
                    ),
                )
            ),
            ModelResponse("done"),
        ],
        delay_s=0,
    )
    settings = ModelSettings(
        execution_mode="readonly" if level == "readonly" else "native",
        approval_policy="never" if level == "full" else "ask",
    )
    approvals = ApprovalManager()
    runtime = AgentRuntime(provider, settings=settings, approvals=approvals)
    thread = ThreadState(tmp_path)
    turn = TurnState(thread.thread_id)
    events = []

    async def emit(event):
        events.append(event)
        if event.kind == EventKind.APPROVAL_REQUESTED:
            request = json.loads(event.text)
            await approvals.respond(request["approval_id"], turn.turn_id, True)

    await runtime.run_turn(thread, turn, "change and test", Event(), emit)
    assert (tmp_path / "a.py").read_bytes() == (
        b"answer = 1\n" if level == "readonly" else b"answer = 2\n"
    )
    requests = [event for event in events if event.kind == EventKind.APPROVAL_REQUESTED]
    assert len(requests) == (2 if level == "default" else 0)
    receipts = [json.loads(message.content) for message in thread.history if message.role == "tool"]
    assert len(receipts) == 2
    assert all(receipt["success"] == (level != "readonly") for receipt in receipts)
    if level != "readonly":
        assert "verified" in json.loads(receipts[-1]["result"])["stdout"]
    automatic = [
        event
        for event in events
        if event.kind == EventKind.APPROVAL_RESOLVED and json.loads(event.text).get("automatic")
    ]
    assert len(automatic) == (2 if level == "full" else 0)


async def test_full_access_still_honors_cancellation_and_path_boundary(tmp_path):
    (tmp_path / "a").write_bytes(b"old\n")
    cancel = Event()

    async def publish(kind, text, call_id):
        if kind == "approval.resolved":
            cancel.set()

    router = ToolRouter(
        make_coding_registry("native"),
        Workspace(tmp_path),
        settings=ModelSettings(execution_mode="native", approval_policy="never"),
        publish=publish,
    )
    result = await router.execute(
        ToolCall("patch", "apply_patch", {"patch": "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n"}),
        cancel,
    )
    assert not result.success and (tmp_path / "a").read_bytes() == b"old\n"
    with pytest.raises(asyncio.CancelledError):
        await router.execute(ToolCall("read", "read_file", {"path": "a"}), cancel)
    result = await router.execute(ToolCall("outside", "read_file", {"path": "../outside"}), Event())
    assert not result.success
