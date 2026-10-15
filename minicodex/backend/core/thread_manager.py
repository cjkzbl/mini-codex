from pathlib import Path

from minicodex.backend.core.history import validate_history
from minicodex.backend.core.models import ThreadState
from minicodex.backend.persistence.memory_store import InMemoryThreadStore, ThreadStore


class ThreadManager:
    def __init__(self, store: ThreadStore | None = None) -> None:
        self.store = store or InMemoryThreadStore()

    def create(self, workspace: Path) -> ThreadState:
        thread = ThreadState(workspace)
        self.store.put(thread)
        return thread

    def resume(self, thread_id: str) -> ThreadState:
        thread = self.store.get(thread_id)
        validate_history(thread.history)
        if not thread.workspace.is_dir():
            raise ValueError("会话工作区已不存在")
        return thread

    def list_threads(self) -> tuple[ThreadState, ...]:
        return self.store.list_threads()

    def rename(self, thread_id: str, title: str) -> ThreadState:
        title = title.strip()
        if not title or len(title) > 80 or "\n" in title or "\r" in title:
            raise ValueError("对话名称需为 1–80 个字符，不能包含换行")
        thread = self.store.get(thread_id)
        old_title, old_custom = thread.title, thread.title_is_custom
        thread.title = title
        thread.title_is_custom = True
        try:
            self.store.put(thread)
        except BaseException:
            thread.title, thread.title_is_custom = old_title, old_custom
            raise
        return thread

    def delete(self, thread_id: str) -> None:
        self.store.delete(thread_id)
