import pytest

from minicodex.backend.core.models import Message, ToolCall
from minicodex.backend.core.thread_manager import ThreadManager


def test_memory_threads_resume_identity_and_separate_histories(tmp_path):
    manager = ThreadManager()
    first, second = manager.create(tmp_path), manager.create(tmp_path)
    first.history.extend([Message("user", "hello"), Message("assistant", "answer")])
    assert manager.resume(first.thread_id) is first
    assert not manager.resume(second.thread_id).history
    assert manager.list_threads() == (second, first)
    with pytest.raises(ValueError):
        manager.resume("unknown")


def test_resume_rejects_unpaired_calls(tmp_path):
    manager = ThreadManager()
    thread = manager.create(tmp_path)
    thread.history.append(Message("assistant", "", (ToolCall("c", "list_files", {}),)))
    with pytest.raises(ValueError):
        manager.resume(thread.thread_id)
