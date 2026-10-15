import asyncio
import json
import sys
from threading import Event

import pytest

from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.models import ToolCall
from minicodex.backend.execution.approval import ApprovalManager
from minicodex.backend.execution.commands import prepare_command, run_plan
from minicodex.backend.execution.workspace import Workspace
from minicodex.backend.tools.coding import make_coding_registry
from minicodex.backend.tools.router import ToolRouter


async def test_foreign_stale_and_expired_approval_cannot_authorize():
    manager = ApprovalManager()
    events = []

    async def publish(kind, text):
        events.append((kind, json.loads(text)))

    task = asyncio.create_task(
        manager.ask(
            turn_id="turn",
            call_id="call",
            tool="run_command",
            mode="native",
            summary="test",
            fingerprint="fingerprint",
            cancel=Event(),
            publish=publish,
        )
    )
    await asyncio.sleep(0)
    identity = events[0][1]["approval_id"]
    await manager.respond(identity, "foreign", True)
    assert not task.done()
    await manager.respond(identity, "turn", True)
    assert await task
    await manager.respond(identity, "turn", True)
    assert not manager.pending
    assert not await manager.ask(
        turn_id="turn",
        call_id="next",
        tool="apply_patch",
        mode="write",
        summary="test",
        fingerprint="expired",
        cancel=Event(),
        publish=publish,
        timeout_s=0.01,
    )


@pytest.mark.parametrize("decision", ["deny", "cancel", "change_parameters", "change_file"])
async def test_rejected_or_changed_action_does_not_write(tmp_path, decision):
    path = tmp_path / "a"
    path.write_bytes(b"old\n")
    manager = ApprovalManager()
    cancel = Event()
    call = ToolCall("call", "apply_patch", {"patch": "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n"})

    async def publish(kind, text, call_id):
        if kind == "approval.requested":
            request = json.loads(text)
            if decision == "cancel":
                cancel.set()
            elif decision == "change_parameters":
                call.arguments["patch"] += "mutated"
            elif decision == "change_file":
                path.write_bytes(b"user edit\n")
            await manager.respond(request["approval_id"], "turn", decision != "deny")

    router = ToolRouter(
        make_coding_registry("write"),
        Workspace(tmp_path),
        settings=ModelSettings(execution_mode="write"),
        approvals=manager,
        turn_id="turn",
        publish=publish,
    )
    reply = await router.execute(call, cancel)
    assert not reply.success
    assert path.read_bytes() == (b"user edit\n" if decision == "change_file" else b"old\n")
    assert not manager.pending


def test_command_snapshot_invalidates_changed_project(tmp_path):
    (tmp_path / "a.py").write_bytes(b"print('old')\n")
    workspace = Workspace(tmp_path)
    plan = prepare_command(
        {"argv": [sys.executable, "-c", "print('not allowed')"]},
        workspace,
        Event(),
        "native",
        "unused",
        10,
    )
    (tmp_path / "a.py").write_bytes(b"print('new')\n")
    with pytest.raises(ValueError, match="变化"):
        run_plan(plan, workspace, Event())


async def test_readonly_cannot_call_writes_even_with_manually_registered_schema(tmp_path):
    router = ToolRouter(make_coding_registry("native"), Workspace(tmp_path))
    reply = await router.execute(
        ToolCall("c", "run_command", {"argv": [sys.executable, "-c", "print('bad')"]}), Event()
    )
    assert not reply.success
    schemas = {item["function"]["name"] for item in make_coding_registry("readonly").schemas()}
    assert "apply_patch" not in schemas and "run_command" not in schemas
