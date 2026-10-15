import json
import os
from contextlib import ExitStack
from threading import Event
from typing import Any

from minicodex.backend.core.models import ToolResult
from minicodex.backend.execution.commands import executable, run_native
from minicodex.backend.execution.windows import api, final_path, locked_handle
from minicodex.backend.execution.workspace import Workspace
from minicodex.backend.tools.read_only import make_registry, walk
from minicodex.backend.tools.registry import ToolRegistry, ToolSpec


def git_tool(operation: str, workspace: Workspace, cancel: Event) -> ToolResult:
    git = executable("git")
    exclusions = [
        f":(exclude,icase,glob)**/{pattern}"
        for pattern in (
            ".env*",
            "*.pem",
            "*.key",
            "*.p12",
            "*.pfx",
            "id_rsa",
            "id_ed25519",
            "credentials",
            ".git/**",
            ".ssh/**",
            ".aws/**",
            ".azure/**",
            ".venv/**",
            "venv/**",
            "node_modules/**",
            "dist/**",
            "build/**",
        )
    ]
    command = [
        git,
        "--no-optional-locks",
        "--no-pager",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "core.untrackedCache=false",
    ]
    listed = run_native([*command, "ls-files", "-z"], workspace.root, 15, cancel)
    if listed["exit_code"] != 0:
        return ToolResult(
            "pending", False, json.dumps(listed, ensure_ascii=False), listed["exit_code"]
        )
    files, truncated = walk(workspace, ".", cancel)
    if truncated or listed["truncated"]:
        raise ValueError("Git 文件范围超过上限，请缩小项目")
    allowed = set(files)
    for name in listed["stdout"].split("\x00"):
        if name:
            try:
                workspace.write_target(name)
                allowed.add(name)
            except (OSError, ValueError):
                pass
    if not allowed:
        return ToolResult("pending", True, json.dumps({"stdout": "", "exit_code": 0}))
    directories = {workspace.root}
    for name in allowed:
        parent = workspace.write_target(name).parent
        while parent != workspace.root:
            directories.add(parent)
            parent = parent.parent
    if operation == "status":
        command += ["status", "--short", "--untracked-files=normal"]
    else:
        command += ["diff", "--no-ext-diff", "--no-textconv", "--no-color"]
    argv = [*command, "--", *(":(literal)" + name for name in sorted(allowed)), *exclusions]
    if sum(len(arg) + 3 for arg in argv) > 24_000:
        raise ValueError("Git 路径列表超过 Windows 参数上限，请缩小项目")
    with ExitStack() as stack:
        for directory in sorted(directories, key=lambda p: len(p.parts)):
            handle = stack.enter_context(locked_handle(directory, directory=True))
            workspace.check_resolved(final_path(api(), handle))
        result = run_native(argv, workspace.root, 15, cancel)
    result["truncated"] = result["truncated"] or len(result["stdout"]) > 16_000
    result["stdout"] = result["stdout"][:16_000]
    result["stderr"] = result["stderr"][:2_000]
    return ToolResult(
        "pending",
        result["exit_code"] == 0 and result["state"] == "completed",
        json.dumps(result, ensure_ascii=False),
        result["exit_code"],
    )


def gated(args: dict[str, Any], workspace: Workspace, cancel: Event) -> ToolResult:
    raise PermissionError("此工具必须通过 Runtime 审批执行")


def make_coding_registry(mode: str) -> ToolRegistry:
    registry = make_registry()
    for name, operation in (("git_status", "status"), ("git_diff", "diff")):

        def execute(
            args: dict[str, Any], workspace: Workspace, cancel: Event, operation: str = operation
        ) -> ToolResult:
            return git_tool(operation, workspace, cancel)

        registry.register(
            ToolSpec(
                name,
                "查看 Git 状态"
                if operation == "status"
                else "查看未暂存修改 diff，不修改 Git 状态",
                {"type": "object", "properties": {}, "additionalProperties": False},
                execute,
            )
        )
    if mode != "readonly":
        registry.register(
            ToolSpec(
                "apply_patch",
                "提出标准 unified diff（--- a/path、+++ b/path、@@）；按当前权限策略应用。"
                "必须先读原文，不覆盖已有修改；父目录需已存在。",
                {
                    "type": "object",
                    "properties": {
                        "patch": {"type": "string", "minLength": 1, "maxLength": 100_000}
                    },
                    "required": ["patch"],
                    "additionalProperties": False,
                },
                gated,
            )
        )
    if mode in {"native", "docker"}:
        registry.register(
            ToolSpec(
                "run_command",
                "执行测试/lint/build。argv 为明确的程序与参数数组，cwd 相对工作区；"
                "遵循当前权限策略。"
                "Docker 使用 Linux 工具链和临时项目副本，改动不写回。",
                {
                    "type": "object",
                    "properties": {
                        "argv": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 64,
                            "items": {"type": "string", "maxLength": 8192},
                        },
                        "cwd": {"type": "string", "minLength": 1, "maxLength": 4096},
                        "timeout_s": {"type": "number", "minimum": 1, "maximum": 180},
                    },
                    "required": ["argv"],
                    "additionalProperties": False,
                },
                gated,
            )
        )
    return registry
