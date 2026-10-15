import asyncio
from collections.abc import Coroutine
from concurrent.futures import Future
from threading import Event, Thread
from typing import Any


class AsyncWorker:
    """One owned event loop; Qt widgets never cross this boundary."""

    def __init__(self) -> None:
        self._ready = Event()
        self._closing = Event()
        self._loop = asyncio.new_event_loop()
        self._thread = Thread(target=self._run, name="minicodex-worker", daemon=False)
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("后台 Worker 启动超时")

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.run_until_complete(self._loop.shutdown_default_executor())
            self._loop.close()

    def submit(self, coroutine: Coroutine[Any, Any, None]) -> Future[None]:
        if self._closing.is_set() or not self._thread.is_alive():
            coroutine.close()
            raise RuntimeError("Worker 已关闭")
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    def close(self, timeout_s: float = 5) -> bool:
        if self._thread.is_alive():
            self._closing.set()
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout_s)
        return not self._thread.is_alive()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()
