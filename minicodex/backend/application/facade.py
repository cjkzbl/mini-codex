import asyncio
from concurrent.futures import Future
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event

from minicodex.backend.application.commands import StartTurn
from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.application.worker import AsyncWorker
from minicodex.backend.core.events import TERMINAL_EVENTS, RuntimeEvent
from minicodex.backend.core.models import ThreadState, TurnState
from minicodex.backend.core.runtime import AgentRuntime
from minicodex.backend.core.thread_manager import ThreadManager
from minicodex.backend.execution.approval import ApprovalManager
from minicodex.backend.llm.base import LLMProvider
from minicodex.backend.llm.fake import FakeLLMProvider
from minicodex.backend.llm.openai_compatible import OpenAICompatibleProvider
from minicodex.backend.persistence.configuration import ConfigurationStore
from minicodex.backend.persistence.credentials import CredentialBackend
from minicodex.backend.persistence.observations import ObservationArchive
from minicodex.backend.persistence.sqlite_store import SQLiteThreadStore


class ApplicationFacade:
    """UI-thread commands, background execution, bounded main-thread event draining."""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        *,
        data_dir: Path | None = None,
        credentials: CredentialBackend | None = None,
    ) -> None:
        self.configuration = ConfigurationStore(data_dir, credentials) if data_dir else None
        self.settings = self.configuration.load() if self.configuration else ModelSettings()
        self.store = SQLiteThreadStore(data_dir) if data_dir else None
        if self.store:
            self.store.secret = self.settings.api_key
        self.observations = ObservationArchive(data_dir / "observations" if data_dir else None)
        self.persistence_error = ""
        self.worker = AsyncWorker()
        self.approvals = ApprovalManager()
        self.runtime = AgentRuntime(
            provider
            or (
                FakeLLMProvider()
                if self.settings.mode == "fake"
                else OpenAICompatibleProvider(self.settings)
            ),
            max_steps=self.settings.max_steps,
            max_tool_calls=self.settings.max_tool_calls,
            max_turn_s=self.settings.max_turn_s,
            settings=self.settings,
            approvals=self.approvals,
            observations=self.observations,
        )
        self.thread_manager = ThreadManager(self.store)
        self.thread: ThreadState | None = None
        self.active_turn: TurnState | None = None
        self._events: Queue[RuntimeEvent] = Queue(maxsize=512)
        self._cancel = Event()
        self._future: Future[None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._shutdown = Event()

    @property
    def is_running(self) -> bool:
        return self.active_turn is not None

    def configure(self, settings: ModelSettings, *, remember_key: bool | None = None) -> None:
        if self.is_running or self._closed:
            raise RuntimeError("请先停止当前任务，再更改模型设置")
        if self.configuration:
            self.configuration.save(
                settings,
                remember=(
                    self.configuration.remember_key if remember_key is None else remember_key
                ),
            )
        model_changed = any(
            getattr(self.settings, name) != getattr(settings, name)
            for name in ("mode", "base_url", "model", "api_key", "streaming", "timeout_s")
        )
        provider: LLMProvider = (
            (FakeLLMProvider() if settings.mode == "fake" else OpenAICompatibleProvider(settings))
            if model_changed
            else self.runtime.provider
        )
        self.runtime = AgentRuntime(
            provider,
            max_steps=settings.max_steps,
            max_tool_calls=settings.max_tool_calls,
            max_turn_s=settings.max_turn_s,
            settings=settings,
            approvals=self.approvals,
            observations=self.observations,
        )
        self.settings = settings
        if self.store:
            self.store.secret = settings.api_key
        if self.thread and model_changed:
            self.thread = self.thread_manager.create(self.thread.workspace)

    def resume(self, thread_id: str) -> ThreadState:
        if self._closed or self.is_running:
            raise RuntimeError("请先停止当前任务，再切换会话")
        self.thread = self.thread_manager.resume(thread_id)
        return self.thread

    def rename_thread(self, thread_id: str, title: str) -> ThreadState:
        if self._closed or self.is_running:
            raise RuntimeError("请先停止当前任务，再重命名对话")
        return self.thread_manager.rename(thread_id, title)

    def select_workspace(self, path: str | Path) -> ThreadState:
        if self._closed:
            raise RuntimeError("客户端已关闭")
        if self.is_running:
            raise RuntimeError("请先停止当前任务，再切换项目")
        raw = Path(path)
        if str(raw).startswith(("\\\\", "//")):
            raise ValueError("首版仅支持本地磁盘目录，不支持网络路径")
        workspace = raw.resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError("请选择项目文件夹")
        self.thread = self.thread_manager.create(workspace)
        if self.configuration:
            try:
                self.configuration.remember_project(workspace)
            except (OSError, ValueError):
                self.configuration.warning = "项目已打开，但最近项目记录未能保存。"
        return self.thread

    def delete_thread(self, thread_id: str) -> ThreadState | None:
        if self._closed or self.is_running:
            raise RuntimeError("请先停止当前任务，再删除对话")
        current = self.thread
        self.thread_manager.delete(thread_id)
        if current and current.thread_id == thread_id:
            self.thread = None
            for candidate in self.thread_manager.list_threads():
                try:
                    self.thread = self.thread_manager.resume(candidate.thread_id)
                    break
                except ValueError:
                    continue
        return self.thread

    def start(self, command: StartTurn) -> TurnState:
        if self.persistence_error:
            raise RuntimeError(self.persistence_error + "；请修复存储后重启客户端")
        if self._closed:
            raise RuntimeError("客户端已关闭")
        if self.is_running:
            raise RuntimeError("当前会话已有运行中的任务")
        thread = self.thread
        if thread is None or command.thread_id != thread.thread_id:
            raise ValueError("请先选择当前项目")
        if not thread.workspace.is_dir():
            raise ValueError("项目目录已不存在，请重新选择")
        if not thread.history and not thread.title_is_custom:
            thread.title = " ".join(command.prompt.split())[:36] or "新对话"
        turn = TurnState(thread.thread_id, prompt=command.prompt)
        self._cancel = Event()
        self.active_turn = turn
        self._future = self.worker.submit(self._execute(thread, turn, command.prompt, self._cancel))
        return turn

    async def _execute(
        self, thread: ThreadState, turn: TurnState, prompt: str, cancel: Event
    ) -> None:
        self._task = asyncio.current_task()
        try:
            await self.runtime.run_turn(thread, turn, prompt, cancel, self._publish)
        finally:
            self._task = None

    async def _publish(self, event: RuntimeEvent) -> None:
        if self.store and not self.persistence_error and self.thread and self.active_turn:
            try:
                self.store.checkpoint(self.thread, self.active_turn, event)
            except Exception as error:
                self.persistence_error = "会话写入失败：" + type(error).__name__
                raise RuntimeError(self.persistence_error) from error
        # Backpressure: never block the loop and never discard events while open.
        # On application shutdown there is no UI consumer, so stop publishing.
        while not self._shutdown.is_set():
            try:
                self._events.put_nowait(event)
                return
            except Full:
                await asyncio.sleep(0.005)

    def cancel(self) -> None:
        if not self.is_running:
            return
        if self._cancel.is_set():
            return
        self._cancel.set()
        self.worker.submit(self._cancel_task())

    def respond_approval(self, approval_id: str, approved: bool) -> None:
        if self.active_turn and not self._cancel.is_set():
            self.worker.submit(
                self.approvals.respond(approval_id, self.active_turn.turn_id, approved)
            )

    async def _cancel_task(self) -> None:
        if self._task is not None:
            self._task.cancel()

    def drain_events(self, limit: int = 64) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = []
        for _ in range(limit):
            try:
                event = self._events.get_nowait()
            except Empty:
                break
            events.append(event)
            if (
                event.kind in TERMINAL_EVENTS
                and self.active_turn is not None
                and event.turn_id == self.active_turn.turn_id
            ):
                self.active_turn = None
        return events

    def close(self) -> bool:
        if self._closed:
            return not self.worker.alive
        self.cancel()
        self._shutdown.set()
        stopped = self.worker.close()
        if stopped:
            if self.store:
                self.store.close()
            self._closed = True
        return stopped

    def export_trace(self, thread_id: str, path: Path, *, include_content: bool = False) -> None:
        if self.is_running:
            raise RuntimeError("请等当前任务结束后导出")
        import json

        from minicodex.backend.core.redaction import redact
        from minicodex.backend.persistence.codec import event_record, message_record, turn_record

        thread = self.thread_manager.store.get(thread_id)
        records: list[dict[str, object]] = [
            {
                "type": "thread",
                "thread_id": thread_id,
                "includes_content": include_content,
            }
        ]
        if include_content:
            records[0]["title"] = thread.title
        for turn in thread.turns:
            state = turn_record(turn)
            if not include_content:
                state.pop("prompt", None)
            records.append({"type": "turn", **state})
            for event in turn.events:
                data = event_record(event)
                if not include_content:
                    data.pop("text", None)
                records.append({"type": "event", **data})
        if include_content:
            records.extend({"type": "message", **message_record(m)} for m in thread.history)
        with path.open("w", encoding="utf-8") as stream:
            for record in records:
                stream.write(
                    redact(json.dumps(record, ensure_ascii=False), self.settings.api_key) + "\n"
                )
