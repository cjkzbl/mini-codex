from typing import Protocol

from minicodex.backend.core.models import ThreadState


class ThreadStore(Protocol):
    def put(self, thread: ThreadState) -> None: ...
    def get(self, thread_id: str) -> ThreadState: ...
    def list_threads(self) -> tuple[ThreadState, ...]: ...
    def delete(self, thread_id: str) -> None: ...


class InMemoryThreadStore:
    def __init__(self) -> None:
        self._threads: dict[str, ThreadState] = {}

    def put(self, thread: ThreadState) -> None:
        self._threads[thread.thread_id] = thread

    def get(self, thread_id: str) -> ThreadState:
        if thread_id not in self._threads:
            raise ValueError("会话不存在")
        return self._threads[thread_id]

    def list_threads(self) -> tuple[ThreadState, ...]:
        return tuple(reversed(tuple(self._threads.values())))

    def delete(self, thread_id: str) -> None:
        self.get(thread_id)
        del self._threads[thread_id]
