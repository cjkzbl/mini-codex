import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Any

from minicodex.backend.execution.windows import WindowsProcess
from minicodex.backend.execution.workspace import Workspace
from minicodex.backend.tools.read_only import walk

OUTPUT_BYTES = 32_000
ENV_KEYS = {
    "SYSTEMROOT",
    "WINDIR",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
    "COMSPEC",
    "SYSTEMDRIVE",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
}


def clean_environment() -> dict[str, str]:
    result = {k: v for k, v in os.environ.items() if k.upper() in ENV_KEYS}
    result.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return result


def executable(value: str) -> str:
    if not value or "\x00" in value:
        raise ValueError("无效 executable")
    found = shutil.which(value, path=clean_environment().get("PATH", ""))
    if found is None:
        raise ValueError("命令工具链未找到，请安装工具或填写可执行程序绝对路径")
    path = Path(found).resolve(strict=True)
    if path.suffix.casefold() not in {".exe", ".com"}:
        raise ValueError("禁止隐式执行 .bat/.cmd；需明确指定解释器并审批完整参数")
    return str(path)


def manifest(workspace: Workspace, cancel: Event) -> tuple[dict[str, bytes], str]:
    files, truncated = walk(workspace, ".", cancel)
    if truncated:
        raise ValueError("项目文件超过执行快照上限，请缩小工作区")
    contents: dict[str, bytes] = {}
    total = 0
    digest = hashlib.sha256()
    for name in files:
        data = workspace.read_bytes(name, cancel)
        total += len(data)
        if total > 64 * 1024 * 1024:
            raise ValueError("执行快照超过 64 MiB，请缩小工作区")
        contents[name] = data
        digest.update(name.encode())
        digest.update(hashlib.sha256(data).digest())
    return contents, digest.hexdigest()


@dataclass(frozen=True)
class CommandPlan:
    argv: tuple[str, ...]
    cwd: str
    timeout_s: float
    mode: str
    image: str
    workspace_digest: str
    fingerprint: str


def prepare_command(
    args: dict[str, Any],
    workspace: Workspace,
    cancel: Event,
    mode: str,
    image: str,
    max_timeout: float,
) -> CommandPlan:
    argv = args["argv"]
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(v, str) or "\x00" in v for v in argv)
        or not argv[0]
    ):
        raise ValueError("argv 必须是非空字符串数组")
    cwd = args.get("cwd", ".")
    workspace.resolve(cwd, directory=True)
    requested_timeout = float(args.get("timeout_s", max_timeout))
    if (
        not math.isfinite(requested_timeout)
        or not math.isfinite(max_timeout)
        or requested_timeout <= 0
        or max_timeout <= 0
    ):
        raise ValueError("命令超时必须为正的有限数值")
    timeout = min(requested_timeout, max_timeout)
    if mode == "native":
        argv = [executable(argv[0]), *argv[1:]]
    elif mode != "docker":
        raise PermissionError("命令执行未开启")
    else:
        docker = executable("docker")
        info = run_native([docker, "info", "--format", "{{.OSType}}"], workspace.root, 15, cancel)
        if info["exit_code"] != 0 or info["stdout"].strip() != "linux":
            raise ValueError("Docker Linux 后端不可用；不会降级为宿主执行")
        inspected = run_native(
            [docker, "image", "inspect", "--format", "{{.Id}}", image], workspace.root, 15, cancel
        )
        if inspected["exit_code"] != 0 or not re.fullmatch(
            r"sha256:[a-f0-9]{64}", inspected["stdout"].strip()
        ):
            raise ValueError("Docker 本地镜像不可用，请先准备镜像；不会自动下载")
        image = inspected["stdout"].strip()
    _, digest = manifest(workspace, cancel)
    payload = json.dumps([argv, cwd, timeout, mode, image, digest], ensure_ascii=False)
    return CommandPlan(
        tuple(argv), cwd, timeout, mode, image, digest, hashlib.sha256(payload.encode()).hexdigest()
    )


def run_native(
    argv: list[str],
    cwd: Path,
    timeout_s: float,
    cancel: Event,
    on_output: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    if cancel.is_set():
        return {
            "state": "cancelled",
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "effects_unknown": False,
        }
    start = time.monotonic()
    process = WindowsProcess(argv, cwd, clean_environment())
    outputs = [bytearray(), bytearray()]
    truncated = [False, False]

    def read(index: int) -> None:
        with process.readers[index] as reader:
            while chunk := reader.read(4096):
                remaining = OUTPUT_BYTES - len(outputs[index])
                kept = chunk[: max(0, remaining)]
                outputs[index].extend(kept)
                if len(chunk) > remaining:
                    truncated[index] = True
                if kept and on_output:
                    on_output(
                        "stdout" if index == 0 else "stderr", kept.decode("utf-8", errors="replace")
                    )

    readers = [Thread(target=read, args=(i,), name=f"minicodex-output-{i}") for i in range(2)]
    for thread in readers:
        thread.start()
    state = "completed"
    code = None
    cleanup = False
    try:
        while (code := process.poll()) is None:
            if cancel.is_set():
                state = "cancelled"
                break
            if time.monotonic() - start >= timeout_s:
                state = "timed_out"
                break
            time.sleep(0.02)
        process.terminate()  # includes descendants after the root exits normally
        deadline = time.monotonic() + 5
        while process.active_count() and time.monotonic() < deadline:
            time.sleep(0.01)
        cleanup = process.active_count() == 0
    finally:
        process.close()
        for thread in readers:
            thread.join(5)
        cleanup = cleanup and not any(thread.is_alive() for thread in readers)
    if not cleanup:
        raise RuntimeError("命令清理未确认，结果未知；请检查进程，不自动重放")
    return {
        "state": state,
        "exit_code": code,
        "stdout": outputs[0].decode("utf-8", errors="replace"),
        "stderr": outputs[1].decode("utf-8", errors="replace"),
        "truncated": any(truncated),
        "duration_s": round(time.monotonic() - start, 3),
        "pid": int(process.info.pid),
        "cleanup_verified": cleanup,
        "effects_unknown": state != "completed",
        "platform": "Windows native",
    }


class DockerExecutor:
    def __init__(self, runner: Callable[..., dict[str, Any]] = run_native) -> None:
        self.runner = runner

    def run(
        self,
        plan: CommandPlan,
        workspace: Workspace,
        cancel: Event,
        on_output: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        if cancel.is_set():
            raise InterruptedError("任务已取消")
        docker = executable("docker")

        def invoke(args: list[str], *, cleanup: bool = False) -> dict[str, Any]:
            return self.runner(
                [docker, *args],
                workspace.root,
                15 if cleanup else plan.timeout_s,
                Event() if cleanup else cancel,
            )

        info = invoke(["info", "--format", "{{.OSType}}"])
        if info["exit_code"] != 0 or info["stdout"].strip() != "linux":
            raise ValueError("Docker Linux 后端不可用；不会降级为宿主执行")
        inspected = invoke(["image", "inspect", "--format", "{{.Id}}", plan.image])
        image_id = inspected["stdout"].strip()
        if inspected["exit_code"] != 0 or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
            raise ValueError("Docker 镜像未就绪，请先准备指定镜像；客户端不会自动下载")
        if image_id != plan.image:
            raise ValueError("Docker 镜像与审批时的固定 ID 不一致，批准已失效")
        contents, digest = manifest(workspace, cancel)
        if digest != plan.workspace_digest:
            raise ValueError("审批后项目内容已变化，命令审批失效")
        import uuid

        name = "minicodex-" + uuid.uuid4().hex
        if cancel.is_set():
            raise InterruptedError("任务已取消")
        with tempfile.TemporaryDirectory(prefix="minicodex-exec-") as folder:
            snapshot = Path(folder)
            for path, content in contents.items():
                if cancel.is_set():
                    raise InterruptedError("任务已取消")
                target = snapshot / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            if "," in str(snapshot):
                raise ValueError("Docker 快照路径不能含逗号")
            if cancel.is_set():
                raise InterruptedError("任务已取消")
            try:
                created = invoke(
                    [
                        "create",
                        "--name",
                        name,
                        "--label",
                        "com.minicodex.sandbox=1",
                        "--pull",
                        "never",
                        "--network",
                        "none",
                        "--read-only",
                        "--no-healthcheck",
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges",
                        "--pids-limit",
                        "64",
                        "--memory",
                        "512m",
                        "--memory-swap",
                        "512m",
                        "--cpus",
                        "1",
                        "--user",
                        "65534:65534",
                        "--tmpfs",
                        "/tmp:rw,nosuid,nodev,size=128m",
                        "--mount",
                        f"type=bind,src={snapshot},dst=/workspace",
                        "--workdir",
                        "/workspace/"
                        + workspace.resolve(plan.cwd, directory=True)
                        .relative_to(workspace.root)
                        .as_posix(),
                        "--env",
                        "PYTHONDONTWRITEBYTECODE=1",
                        "--env",
                        "PYTHONIOENCODING=utf-8",
                        "--entrypoint",
                        plan.argv[0],
                        image_id,
                        *plan.argv[1:],
                    ]
                )
                if created["exit_code"] != 0:
                    raise ValueError("Docker 创建失败；请检查镜像与路径，不会降级宿主执行")
                result = self.runner(
                    [docker, "start", "--attach", name],
                    workspace.root,
                    plan.timeout_s,
                    cancel,
                    on_output,
                )
                state = invoke(["inspect", "--format", "{{json .State}}", name], cleanup=True)
                if result["state"] == "completed":
                    try:
                        details = json.loads(state["stdout"])
                        finished = (
                            state["exit_code"] == 0
                            and isinstance(details, dict)
                            and details.get("Running") is False
                            and details.get("Status") == "exited"
                            and not details.get("Error")
                            and type(details.get("ExitCode")) is int
                        )
                    except (TypeError, ValueError):
                        finished = False
                    if not finished:
                        raise RuntimeError(f"容器退出状态未确认：{name}；结果未知；禁止自动重放")
                    result["exit_code"] = details["ExitCode"]
                else:
                    result["exit_code"] = None
                result.update(
                    {
                        "platform": "Docker Linux",
                        "image_id": image_id,
                        "workspace_mode": "filtered temporary copy; changes discarded",
                        "effects_unknown": False,
                    }
                )
                return result
            finally:
                cleanup_error: Exception | None = None
                try:
                    invoke(["rm", "--force", "--volumes", name], cleanup=True)
                except (OSError, RuntimeError) as error:
                    cleanup_error = error
                try:
                    inspected = invoke(["inspect", "--format", "{{.Id}}", name], cleanup=True)
                except (OSError, RuntimeError) as error:
                    raise RuntimeError(
                        f"容器清理未确认：{name}；结果未知，需手动清理；禁止自动重放"
                    ) from error
                absent = (
                    inspected.get("state") == "completed"
                    and inspected["exit_code"] != 0
                    and any(
                        line.strip().casefold()
                        in {
                            f"error: no such object: {name}",
                            f"error: no such container: {name}",
                            f"error response from daemon: no such container: {name}",
                            f"no such object: {name}",
                        }
                        for line in inspected["stderr"].splitlines()
                    )
                )
                if not absent:
                    raise RuntimeError(
                        f"容器清理未确认：{name}；结果未知，需手动清理；禁止自动重放"
                    ) from cleanup_error


def run_plan(
    plan: CommandPlan,
    workspace: Workspace,
    cancel: Event,
    on_output: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    before, digest = manifest(workspace, cancel)
    if digest != plan.workspace_digest:
        raise ValueError("审批后项目内容已变化，命令审批失效")
    if plan.mode == "docker":
        return DockerExecutor().run(plan, workspace, cancel, on_output)
    result = run_native(
        list(plan.argv),
        workspace.resolve(plan.cwd, directory=True),
        plan.timeout_s,
        cancel,
        on_output,
    )
    # After Job cleanup, cancellation must not suppress recording already made edits.
    # Reuse the protected, bounded project manifest; never inspect paths outside it.
    try:
        after, _ = manifest(workspace, Event())
        result["edited_files"] = sorted(
            name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
        )
        result["file_metrics_complete"] = True
    except (OSError, ValueError) as error:
        result["edited_files"] = []
        result["file_metrics_complete"] = False
        result["file_metrics_error"] = str(error)
    return result
