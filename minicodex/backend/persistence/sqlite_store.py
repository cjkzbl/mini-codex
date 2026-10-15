import json
import os
import sqlite3
import time
from pathlib import Path
from threading import RLock
from typing import Any

from minicodex.backend.core.events import TERMINAL_EVENTS, EventKind, RuntimeEvent
from minicodex.backend.core.history import validate_history
from minicodex.backend.core.models import Message, ThreadState, TurnState, TurnStatus
from minicodex.backend.core.redaction import redact
from minicodex.backend.persistence.codec import (
    event_record,
    message_record,
    read_event,
    read_message,
    read_turn,
    turn_record,
)


class SQLiteThreadStore:
    """SQLite is authoritative; metadata-only JSONL is an append-only audit view."""

    def __init__(self, directory: Path, *, readonly: bool = False) -> None:
        self.directory = directory.resolve()
        if not readonly:
            self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.secret = ""
        database = self.directory / "sessions.sqlite3"
        self.db = sqlite3.connect(
            database.as_uri() + "?mode=ro" if readonly else str(database),
            uri=readonly,
            check_same_thread=False,
        )
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in {0, 1}:
            self.db.close()
            raise ValueError("会话数据库版本较新，请使用对应版本客户端；未修改数据库")
        if readonly and version != 1:
            self.db.close()
            raise ValueError("没有可读取的 M3 会话数据库")
        if not readonly:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY, workspace TEXT NOT NULL, title TEXT NOT NULL,
                custom INTEGER NOT NULL, history TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS turns (
                id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, state TEXT NOT NULL, ordinal INTEGER);
            CREATE TABLE IF NOT EXISTS events (
                turn_id TEXT NOT NULL, sequence INTEGER NOT NULL, record TEXT NOT NULL,
                PRIMARY KEY(turn_id, sequence));
            PRAGMA user_version=1;
        """)
        self._threads: dict[str, ThreadState] = {}
        self.audit_warning = ""
        try:
            with self.db:
                self.db.execute("BEGIN")
                self._load()
            if not readonly:
                self._recover()
        except BaseException:
            self.db.close()
            raise

    def wire(self, value: Any) -> str:
        def clean(item: Any) -> Any:
            if isinstance(item, str):
                return redact(item, self.secret)
            if isinstance(item, dict):
                return {redact(str(k), self.secret): clean(v) for k, v in item.items()}
            if isinstance(item, (tuple, list)):
                return [clean(v) for v in item]
            return item

        return json.dumps(clean(value), ensure_ascii=False)

    def _load(self) -> None:
        for identity, workspace, title, custom, history in self.db.execute(
            "SELECT id,workspace,title,custom,history FROM threads ORDER BY updated"
        ):
            thread = ThreadState(
                Path(workspace),
                identity,
                [read_message(m) for m in json.loads(history)],
                title,
                bool(custom),
            )
            for turn_id, state in self.db.execute(
                "SELECT id,state FROM turns WHERE thread_id=? ORDER BY ordinal", (identity,)
            ):
                turn = read_turn(json.loads(state))
                turn.events = [
                    read_event(json.loads(row[0]))
                    for row in self.db.execute(
                        "SELECT record FROM events WHERE turn_id=? ORDER BY sequence", (turn_id,)
                    )
                ]
                thread.turns.append(turn)
            self._threads[identity] = thread

    def _save_thread(self, thread: ThreadState) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO threads VALUES(?,?,?,?,?,?)",
            (
                thread.thread_id,
                str(thread.workspace),
                redact(thread.title, self.secret),
                int(thread.title_is_custom),
                self.wire([message_record(m) for m in thread.history]),
                time.time(),
            ),
        )

    def put(self, thread: ThreadState) -> None:
        with self.lock, self.db:
            self._threads[thread.thread_id] = thread
            self._save_thread(thread)
            for ordinal, turn in enumerate(thread.turns):
                self.db.execute(
                    "INSERT OR REPLACE INTO turns VALUES(?,?,?,?)",
                    (
                        turn.turn_id,
                        thread.thread_id,
                        self.wire(turn_record(turn)),
                        ordinal,
                    ),
                )
                self.db.executemany(
                    "INSERT OR IGNORE INTO events VALUES(?,?,?)",
                    [
                        (turn.turn_id, event.sequence, self.wire(event_record(event)))
                        for event in turn.events
                    ],
                )

    def checkpoint(self, thread: ThreadState, turn: TurnState, event: RuntimeEvent) -> None:
        with self.lock, self.db:
            if event.kind != EventKind.MODEL_DELTA:
                self._save_thread(thread)
            self.db.execute(
                "INSERT OR REPLACE INTO turns VALUES(?,?,?,?)",
                (
                    turn.turn_id,
                    thread.thread_id,
                    self.wire(turn_record(turn)),
                    thread.turns.index(turn),
                ),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO events VALUES(?,?,?)",
                (
                    turn.turn_id,
                    event.sequence,
                    self.wire(event_record(event)),
                ),
            )
        # Keep prompts, model bodies, argv, file content and environment out of default traces.
        audit = {k: v for k, v in event_record(event).items() if k != "text"}
        audit["at"] = time.time()
        audit["model_calls"] = turn.model_calls
        audit["tool_calls"] = turn.tool_calls
        audit["input_tokens_estimated"] = turn.input_tokens_estimated
        try:
            with (self.directory / "trace.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(self.wire(audit) + "\n")
                stream.flush()
                if event.kind in TERMINAL_EVENTS or event.kind == EventKind.TOOL_STARTED:
                    os.fsync(stream.fileno())
        except OSError as error:
            self.audit_warning = f"JSONL 追踪写入失败；会话已保存在 SQLite：{error}"

    def _recover(self) -> None:
        for thread in self._threads.values():
            pending: set[str] = set()
            for message in thread.history:
                pending.update(call.call_id for call in message.tool_calls)
                if message.role == "tool":
                    pending.discard(message.tool_call_id or "")
            interrupted = [
                t
                for t in thread.turns
                if t.status
                in {
                    TurnStatus.PENDING,
                    TurnStatus.RUNNING,
                }
            ]
            if pending and not interrupted:
                raise ValueError("会话历史损坏：终止任务存在悬空回执；原数据库已保留")
            if not interrupted:
                validate_history(thread.history)
                continue
            explanation = (
                "上次进程中断，执行结果未知。先检查文件与 diff；不会自动重放工具或沿用审批。"
            )
            for call_id in pending:
                thread.history.append(
                    Message(
                        "tool",
                        json.dumps(
                            {
                                "success": False,
                                "state": "unknown",
                                "error": explanation,
                            },
                            ensure_ascii=False,
                        ),
                        tool_call_id=call_id,
                    )
                )
            thread.history.append(Message("assistant", explanation))
            validate_history(thread.history)
            for turn in interrupted:
                turn.status, turn.error = TurnStatus.INTERRUPTED, explanation
                turn.file_metrics_complete = False
                sequence = max((e.sequence for e in turn.events), default=0) + 1
                step = turn.events[-1].step_id if turn.events else turn.turn_id
                event = RuntimeEvent(
                    EventKind.TURN_INTERRUPTED,
                    thread.thread_id,
                    turn.turn_id,
                    step,
                    sequence,
                    explanation,
                )
                turn.events.append(event)
                self.checkpoint(thread, turn, event)

    def get(self, thread_id: str) -> ThreadState:
        with self.lock:
            try:
                return self._threads[thread_id]
            except KeyError:
                raise ValueError("会话不存在") from None

    def list_threads(self) -> tuple[ThreadState, ...]:
        with self.lock:
            return tuple(reversed(tuple(self._threads.values())))

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def delete(self, thread_id: str) -> None:
        with self.lock:
            self.get(thread_id)
            try:
                with self.db:
                    self.db.execute(
                        "DELETE FROM events WHERE turn_id IN "
                        "(SELECT id FROM turns WHERE thread_id=?)",
                        (thread_id,),
                    )
                    self.db.execute("DELETE FROM turns WHERE thread_id=?", (thread_id,))
                    self.db.execute("DELETE FROM threads WHERE id=?", (thread_id,))
            except sqlite3.Error as error:
                raise OSError("会话删除失败，原会话已保留") from error
            del self._threads[thread_id]
