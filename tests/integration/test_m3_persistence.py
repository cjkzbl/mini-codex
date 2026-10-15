import json
import os
import sqlite3
import subprocess
import sys

import pytest

from minicodex.backend.application.commands import StartTurn
from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.events import EventKind
from minicodex.backend.core.history import validate_history
from minicodex.backend.core.models import ModelResponse, ToolCall, TurnStatus
from minicodex.backend.llm.fake import FakeLLMProvider
from minicodex.backend.persistence.sqlite_store import SQLiteThreadStore


def complete(qtbot, facade, prompt="persist this conversation"):
    turn = facade.start(StartTurn(facade.thread.thread_id, prompt))
    events = []

    def drain():
        events.extend(facade.drain_events())
        return not facade.is_running

    qtbot.waitUntil(drain, timeout=10000)
    return turn, events


def test_restart_preserves_names_history_steps_and_metrics(qtbot, tmp_path):
    project = tmp_path / "中文 project"
    project.mkdir()
    data = tmp_path / "data"
    facade = ApplicationFacade(FakeLLMProvider(delay_s=0), data_dir=data)
    first = facade.select_workspace(project)
    facade.rename_thread(first.thread_id, "独立名称")
    turn, events = complete(qtbot, facade)
    second = facade.select_workspace(project)
    assert facade.close()
    restored = ApplicationFacade(data_dir=data)
    try:
        saved = restored.resume(first.thread_id)
        assert saved.title == "独立名称" and saved.title_is_custom
        assert saved.history == first.history
        assert saved.turns[0].events == events and saved.turns[0].duration_s == turn.duration_s
        assert saved.turns[0].input_tokens_estimated > 0
        assert not restored.resume(second.thread_id).history
        assert restored.approvals.pending == {}
        validate_history(saved.history)
        continued, _ = complete(qtbot, restored, "new independent turn")
        assert continued.status == TurnStatus.COMPLETED
    finally:
        restored.close()


def test_known_key_redacted_and_trace_content_requires_explicit_export(qtbot, tmp_path):
    data = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    secret = 'synthetic-secret-"quoted-value'
    facade = ApplicationFacade(data_dir=data)
    facade.configure(ModelSettings(api_key=secret))
    facade.select_workspace(project)
    turn, _ = complete(qtbot, facade, "PRIVATE_USER_PROMPT " + secret)
    facade.export_trace(facade.thread.thread_id, tmp_path / "metadata.jsonl")
    facade.export_trace(facade.thread.thread_id, tmp_path / "content.jsonl", include_content=True)
    assert "PRIVATE_USER_PROMPT" not in (tmp_path / "metadata.jsonl").read_text("utf-8")
    assert "PRIVATE_USER_PROMPT" not in (data / "trace.jsonl").read_text("utf-8")
    assert "PRIVATE_USER_PROMPT" in (tmp_path / "content.jsonl").read_text("utf-8")
    for record in (tmp_path / "content.jsonl").read_text("utf-8").splitlines():
        assert secret not in json.dumps(json.loads(record), ensure_ascii=False)
        if json.loads(record).get("role") == "user":
            assert "[REDACTED]" in json.loads(record)["content"]
    identity = facade.thread.thread_id
    assert turn.status == TurnStatus.COMPLETED and facade.close()
    store = SQLiteThreadStore(data)
    try:
        assert secret not in store.get(identity).history[0].content
        assert "[REDACTED]" in store.get(identity).history[0].content
    finally:
        store.close()


def test_failed_durable_checkpoint_prevents_patch_execution(qtbot, tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a").write_bytes(b"old\n")
    facade = ApplicationFacade(data_dir=tmp_path / "data")
    facade.configure(ModelSettings(execution_mode="write"))
    facade.runtime.provider = FakeLLMProvider(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        "change",
                        "apply_patch",
                        {"patch": "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n"},
                    ),
                )
            )
        ],
        delay_s=0,
    )
    facade.select_workspace(project)
    original = facade.store.checkpoint

    def fail(thread, turn, event):
        if event.kind == EventKind.TOOL_STARTED:
            raise OSError("synthetic disk failure")
        original(thread, turn, event)

    monkeypatch.setattr(facade.store, "checkpoint", fail)
    try:
        turn, _ = complete(qtbot, facade)
        assert turn.status == TurnStatus.FAILED and "会话写入失败" in turn.error
        assert (project / "a").read_bytes() == b"old\n" and not facade.approvals.pending
        with pytest.raises(RuntimeError, match="重启"):
            facade.start(StartTurn(facade.thread.thread_id, "retry"))
    finally:
        facade.close()


def test_process_crash_recovers_unknown_call_without_replay(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    data = tmp_path / "data"
    source = r"""
import json,os,sys,time
from pathlib import Path
from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.application.commands import StartTurn
from minicodex.backend.core.models import ModelResponse,ToolCall
from minicodex.backend.core.events import EventKind
from minicodex.backend.llm.fake import FakeLLMProvider
f=ApplicationFacade(data_dir=Path(sys.argv[1]))
f.configure(ModelSettings(execution_mode="native"))
command=("from pathlib import Path;import time;Path('made.txt').write_text('once');"
         "print('made',flush=True);time.sleep(30)")
f.runtime.provider=FakeLLMProvider([ModelResponse(tool_calls=(
    ToolCall("unknown-command","run_command",{"argv":[sys.executable,"-c",command]}),
))],delay_s=0)
t=f.select_workspace(sys.argv[2]);f.start(StartTurn(t.thread_id,"crash fixture"))
deadline=time.monotonic()+10
while time.monotonic()<deadline:
    for e in f.drain_events():
        if e.kind==EventKind.APPROVAL_REQUESTED:
            f.respond_approval(json.loads(e.text)["approval_id"],True)
        if e.kind==EventKind.COMMAND_OUTPUT:os._exit(23)
    time.sleep(.01)
os._exit(24)
"""
    process = subprocess.run(
        [sys.executable, "-c", source, str(data), str(project)], capture_output=True, timeout=15
    )
    assert process.returncode == 23, process.stderr.decode(errors="replace")
    store = SQLiteThreadStore(data)
    try:
        thread = store.list_threads()[0]
        assert thread.turns[0].status == TurnStatus.INTERRUPTED
        assert thread.turns[0].events[-1].kind == EventKind.TURN_INTERRUPTED
        result = next(m for m in thread.history if m.tool_call_id == "unknown-command")
        assert json.loads(result.content)["state"] == "unknown"
        assert (project / "made.txt").read_text() == "once"
        validate_history(thread.history)
        assert not thread.turns[0].file_metrics_complete
    finally:
        store.close()
    again = SQLiteThreadStore(data)
    assert len(again.list_threads()[0].turns[0].events) == len(thread.turns[0].events)
    again.close()


def test_newer_schema_is_rejected_without_overwrite(tmp_path):
    db = sqlite3.connect(tmp_path / "sessions.sqlite3")
    db.execute("PRAGMA user_version=999")
    db.close()
    with pytest.raises(ValueError, match="较新"):
        SQLiteThreadStore(tmp_path)
    db = sqlite3.connect(tmp_path / "sessions.sqlite3")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 999
    db.close()


def test_readonly_trace_does_not_recover_an_active_turn(qtbot, tmp_path):
    facade = ApplicationFacade(FakeLLMProvider(delay_s=10), data_dir=tmp_path / "data")
    facade.select_workspace(tmp_path)
    facade.start(StartTurn(facade.thread.thread_id, "wait"))
    qtbot.waitUntil(lambda: (tmp_path / "data" / "trace.jsonl").exists(), timeout=3000)
    store = SQLiteThreadStore(tmp_path / "data", readonly=True)
    try:
        assert store.list_threads()[0].turns[0].status == TurnStatus.RUNNING
        assert facade.is_running
    finally:
        store.close()
        facade.close()


def test_trace_failure_warns_but_database_remains_recoverable(qtbot, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "trace.jsonl").mkdir()
    facade = ApplicationFacade(FakeLLMProvider(delay_s=0), data_dir=data)
    thread = facade.select_workspace(tmp_path)
    turn, _ = complete(qtbot, facade)
    assert turn.status == TurnStatus.COMPLETED and facade.store.audit_warning
    facade.close()
    restored = SQLiteThreadStore(data)
    assert restored.get(thread.thread_id).turns[0].status == TurnStatus.COMPLETED
    restored.close()


def test_trace_cli_reads_saved_session_without_prompt_text(qtbot, tmp_path):
    facade = ApplicationFacade(FakeLLMProvider(delay_s=0), data_dir=tmp_path / "data")
    thread = facade.select_workspace(tmp_path)
    complete(qtbot, facade, "CLI_PRIVATE_PROMPT")
    facade.close()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "minicodex",
            "trace",
            "show",
            thread.thread_id,
            "--data-dir",
            str(tmp_path / "data"),
        ],
        capture_output=True,
        encoding="utf-8",
        timeout=10,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stderr
    assert "CLI_PRIVATE_PROMPT" not in result.stdout
    records = [json.loads(row) for row in result.stdout.splitlines()]
    assert records[0]["status"] == "completed"
    assert records[-1]["kind"] == "turn.completed"
