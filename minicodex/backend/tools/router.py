import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import replace
from queue import Empty, Full, Queue
from threading import Event

from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.models import ToolCall, ToolResult
from minicodex.backend.execution.approval import ApprovalManager
from minicodex.backend.execution.commands import prepare_command, run_plan
from minicodex.backend.execution.patch import apply_plan, prepare_patch
from minicodex.backend.execution.workspace import Workspace
from minicodex.backend.tools.registry import ToolRegistry


class ToolRouter:
    def __init__(
        self,
        registry: ToolRegistry,
        workspace: Workspace,
        *,
        settings: ModelSettings | None = None,
        approvals: ApprovalManager | None = None,
        turn_id: str = "",
        publish: Callable[[str, str, str], Awaitable[None]] | None = None,
    ) -> None:
        self.registry, self.workspace = registry, workspace
        self.settings = settings or ModelSettings()
        self.approvals = approvals
        self.turn_id = turn_id
        self.publish = publish

    async def joined(self, function: Callable[..., object], *args: object) -> object:
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Cancellation must not publish a terminal event while a tool still runs.
            cancel = next((value for value in args if isinstance(value, Event)), None)
            if cancel is not None:
                cancel.set()
            return await asyncio.shield(task)

    async def notify(self, kind: str, text: str, call_id: str) -> None:
        if self.publish:
            await self.publish(kind, text, call_id)

    async def action(self, call: ToolCall, cancel: Event) -> ToolResult:
        arguments = deepcopy(call.arguments)
        wire = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        mode = self.settings.execution_mode
        if mode == "readonly":
            raise PermissionError("当前为只读模式，请在设置中显式开启写入或执行")
        if call.name == "apply_patch":
            assert isinstance(arguments["patch"], str)
            plan = await asyncio.to_thread(
                prepare_patch, arguments["patch"], self.workspace, cancel
            )
            summary = "文件：" + ", ".join(c.path for c in plan.changes) + "\n\n" + plan.diff
            fingerprint = plan.fingerprint
            await self.notify("diff", plan.diff, call.call_id)
        else:
            command = await asyncio.to_thread(
                prepare_command,
                arguments,
                self.workspace,
                cancel,
                mode,
                self.settings.docker_image,
                self.settings.command_timeout_s,
            )
            summary = json.dumps(
                {
                    "argv": command.argv,
                    "cwd": str(self.workspace.resolve(command.cwd)),
                    "timeout_s": command.timeout_s,
                    "mode": mode,
                    "image": command.image if mode == "docker" else None,
                },
                ensure_ascii=False,
                indent=2,
            )
            if mode == "native":
                summary += "\n\n原生执行无 OS 沙箱，仅用于您明确可信的项目。"
            else:
                summary += "\n\n在过滤后的临时项目副本执行，容器改动不会写回原项目。"
            fingerprint = command.fingerprint
        fingerprint = hashlib.sha256((call.name + wire + fingerprint).encode()).hexdigest()

        async def approval_publish(kind: str, text: str) -> None:
            await self.notify("approval." + kind, text, call.call_id)

        if self.settings.approval_policy == "never":
            approved = not cancel.is_set()
            if approved:
                await approval_publish(
                    "resolved",
                    json.dumps(
                        {
                            "approval_id": "",
                            "state": "已批准",
                            "automatic": True,
                            "summary": "完全访问：自动执行本次操作",
                        },
                        ensure_ascii=False,
                    ),
                )
        else:
            if self.approvals is None:
                raise PermissionError("审批服务不可用，未执行操作")
            approved = await self.approvals.ask(
                turn_id=self.turn_id,
                call_id=call.call_id,
                tool=call.name,
                mode=mode,
                summary=summary,
                fingerprint=fingerprint,
                cancel=cancel,
                publish=approval_publish,
            )
        if not approved or cancel.is_set():
            return ToolResult(
                call.call_id, False, json.dumps({"state": "denied_or_expired", "executed": False})
            )
        if json.dumps(call.arguments, ensure_ascii=False, sort_keys=True) != wire:
            raise ValueError("审批后参数变化，批准已失效")
        if call.name == "apply_patch":
            changed = await self.joined(apply_plan, plan, self.workspace, cancel)
            return ToolResult(
                call.call_id,
                True,
                json.dumps({"state": "applied", "files": changed}, ensure_ascii=False),
            )
        output: Queue[tuple[str, str]] = Queue(maxsize=64)

        def on_output(channel: str, text: str) -> None:
            try:
                output.put_nowait((channel, text))
            except Full:
                pass  # full bounded output is returned in the final receipt

        task = asyncio.create_task(
            asyncio.to_thread(run_plan, command, self.workspace, cancel, on_output)
        )
        try:
            while not task.done():
                await self.flush_output(output, call.call_id)
                await asyncio.sleep(0.02)
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            cancel.set()
            result = await asyncio.shield(task)
        await self.flush_output(output, call.call_id)
        success = result["state"] == "completed" and result["exit_code"] == 0
        return ToolResult(
            call.call_id, success, json.dumps(result, ensure_ascii=False), result["exit_code"]
        )

    async def flush_output(self, output: Queue[tuple[str, str]], call_id: str) -> None:
        while True:
            try:
                channel, text = output.get_nowait()
            except Empty:
                return
            await self.notify("command", channel + "\n" + text, call_id)

    async def execute(self, call: ToolCall, cancel: Event) -> ToolResult:
        if cancel.is_set():
            raise asyncio.CancelledError
        try:
            spec = self.registry.get(call.name, call.arguments)
            if call.name in {"apply_patch", "run_command"}:
                return await self.action(call, cancel)
            observation = await self.joined(spec.execute, call.arguments, self.workspace, cancel)
            assert isinstance(observation, ToolResult)
            if cancel.is_set():
                raise asyncio.CancelledError
            return replace(observation, call_id=call.call_id)
        except (OSError, ValueError) as error:
            return ToolResult(
                call.call_id, False, json.dumps({"error": str(error)}, ensure_ascii=False)
            )
