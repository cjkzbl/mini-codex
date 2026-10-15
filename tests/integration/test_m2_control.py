import json
import subprocess
import sys
from threading import Event

import pytest

from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.events import EventKind
from minicodex.backend.core.history import validate_history
from minicodex.backend.core.models import (
    ModelResponse,
    ThreadState,
    ToolCall,
    TurnState,
    TurnStatus,
)
from minicodex.backend.core.runtime import AgentRuntime
from minicodex.backend.execution.approval import ApprovalManager
from minicodex.backend.execution.commands import executable
from minicodex.backend.execution.workspace import Workspace
from minicodex.backend.llm.fake import FakeLLMProvider
from minicodex.backend.tools.coding import git_tool


def test_git_diff_preserves_user_changes_filters_secrets_and_avoids_junctions(tmp_path):
    git = executable("git")

    def invoke(*args):
        return subprocess.run([git, *args], cwd=tmp_path, check=True, capture_output=True)

    invoke("init")
    invoke("config", "user.name", "Test")
    invoke("config", "user.email", "test@example.invalid")
    (tmp_path / "calc.py").write_bytes(b"original\n")
    (tmp_path / ".env").write_bytes(b"PRIVATE_OLD\n")
    invoke("add", "calc.py", ".env")
    invoke("commit", "-m", "fixture")
    (tmp_path / "calc.py").write_bytes(b"user modification\n")
    (tmp_path / ".env").write_bytes(b"PRIVATE_NEW\n")
    workspace = Workspace(tmp_path)
    diff = git_tool("diff", workspace, Event())
    assert diff.success
    text = json.loads(diff.content)["stdout"]
    assert "+user modification" in text and "PRIVATE" not in text
    assert (tmp_path / "calc.py").read_bytes() == b"user modification\n"
    status = git_tool("status", workspace, Event())
    assert status.success and "calc.py" in status.content
    assert ".env" not in status.content


@pytest.mark.parametrize("steps", [1, 20])
async def test_turn_timeout_waits_for_command_cleanup_and_is_failed(tmp_path, steps):
    provider = FakeLLMProvider(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        "command",
                        "run_command",
                        {
                            "argv": [
                                sys.executable,
                                "-c",
                                "import time;print('started',flush=True);time.sleep(30)",
                            ]
                        },
                    ),
                )
            )
        ],
        delay_s=0,
    )
    approvals = ApprovalManager()
    thread = ThreadState(tmp_path)
    turn = TurnState(thread.thread_id)
    events = []

    async def emit(event):
        events.append(event)
        if event.kind == EventKind.APPROVAL_REQUESTED:
            request = json.loads(event.text)
            await approvals.respond(request["approval_id"], turn.turn_id, True)

    runtime = AgentRuntime(
        provider,
        settings=ModelSettings(execution_mode="native"),
        approvals=approvals,
        max_turn_s=0.4,
        max_steps=steps,
    )
    await runtime.run_turn(thread, turn, "test timeout", Event(), emit)
    assert turn.status == TurnStatus.FAILED and "超时" in turn.error
    receipt = next(m for m in thread.history if m.role == "tool")
    assert json.loads(json.loads(receipt.content)["result"])["cleanup_verified"]
    assert events[-1].kind == EventKind.TURN_FAILED
    validate_history(thread.history)


async def test_denied_identical_action_not_reasked_in_same_turn(tmp_path):
    (tmp_path / "a").write_bytes(b"old\n")
    args = {"patch": "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n"}
    provider = FakeLLMProvider(
        [
            ModelResponse(tool_calls=(ToolCall("first", "apply_patch", args),)),
            ModelResponse(tool_calls=(ToolCall("second", "apply_patch", args),)),
            ModelResponse(text="Operation denied."),
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
            await approvals.respond(request["approval_id"], turn.turn_id, False)

    await AgentRuntime(
        provider, settings=ModelSettings(execution_mode="write"), approvals=approvals
    ).run_turn(thread, turn, "change file", Event(), emit)
    assert len(requests) == 1 and (tmp_path / "a").read_bytes() == b"old\n"
    assert turn.status == TurnStatus.COMPLETED
