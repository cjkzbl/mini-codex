import sqlite3

import pytest

from minicodex.backend.core.events import EventKind, RuntimeEvent
from minicodex.backend.core.models import Message, TurnState, TurnStatus
from minicodex.backend.core.thread_manager import ThreadManager
from minicodex.backend.persistence.sqlite_store import SQLiteThreadStore


def populate(manager, path):
    thread = manager.create(path)
    thread.history.extend([Message("user", "question"), Message("assistant", "answer")])
    turn = TurnState(thread.thread_id, status=TurnStatus.COMPLETED)
    turn.events.append(
        RuntimeEvent(EventKind.TURN_COMPLETED, thread.thread_id, turn.turn_id, "step", 1, "done")
    )
    thread.turns.append(turn)
    manager.store.put(thread)
    return thread


@pytest.mark.parametrize("persistent", [False, True])
def test_delete_removes_selected_history_turns_and_events_only(tmp_path, persistent):
    store = SQLiteThreadStore(tmp_path / "data") if persistent else None
    manager = ThreadManager(store)
    first, survivor = populate(manager, tmp_path), populate(manager, tmp_path)
    manager.delete(first.thread_id)
    assert manager.list_threads() == (survivor,)
    with pytest.raises(ValueError):
        manager.resume(first.thread_id)
    if store:
        assert store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert store.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
        store.close()
        restored = SQLiteThreadStore(tmp_path / "data")
        try:
            assert [thread.thread_id for thread in restored.list_threads()] == [survivor.thread_id]
            assert len(restored.get(survivor.thread_id).history) == 2
        finally:
            restored.close()


def test_partial_delete_failure_rolls_back_database_and_memory(tmp_path):
    store = SQLiteThreadStore(tmp_path / "data")
    manager = ThreadManager(store)
    thread = populate(manager, tmp_path)

    def deny_turn_delete(action, table, column, database, trigger):
        return (
            sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_DELETE and table == "turns"
            else sqlite3.SQLITE_OK
        )

    try:
        store.db.set_authorizer(deny_turn_delete)
        with pytest.raises(OSError, match="原会话已保留"):
            manager.delete(thread.thread_id)
        store.db.set_authorizer(None)
        assert manager.resume(thread.thread_id) is thread
        assert store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert store.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
        assert store.db.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 1
    finally:
        store.close()
