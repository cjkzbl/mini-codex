import fnmatch
import json
import os
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any

from minicodex.backend.core.models import ToolResult
from minicodex.backend.execution.workspace import Workspace, protected
from minicodex.backend.tools.registry import ToolRegistry, ToolSpec

OUTPUT_LIMIT = 16_000
SCAN_LIMIT = 10_000


def result(data: dict[str, Any]) -> ToolResult:
    return ToolResult("pending", True, json.dumps(data, ensure_ascii=False))


def walk(workspace: Workspace, root: str, cancel: Event) -> tuple[list[str], bool]:
    base = workspace.resolve(root, directory=True)
    stack = [base]
    files: list[str] = []
    visited = 0
    while stack:
        if cancel.is_set():
            raise InterruptedError("任务已取消")
        folder = stack.pop()
        try:
            workspace.resolve(folder.relative_to(workspace.root).as_posix(), directory=True)
            with os.scandir(folder) as entries:
                for entry in entries:
                    visited += 1
                    if visited > SCAN_LIMIT:
                        return sorted(files), True
                    if cancel.is_set():
                        raise InterruptedError("任务已取消")
                    relative = Path(entry.path).relative_to(workspace.root).as_posix()
                    if protected(tuple(relative.split("/"))):
                        continue
                    try:
                        safe = workspace.resolve(relative)
                        if safe.is_dir():
                            stack.append(safe)
                        elif safe.is_file():
                            files.append(relative)
                    except (PermissionError, FileNotFoundError):
                        continue
        except (PermissionError, FileNotFoundError):
            continue
    return sorted(files), False


def list_files(args: dict[str, Any], workspace: Workspace, cancel: Event) -> ToolResult:
    files, truncated = walk(workspace, args.get("root", "."), cancel)
    matches = [path for path in files if fnmatch.fnmatchcase(path, args.get("glob", "*"))]
    maximum = args.get("max_results", 200)
    selected: list[str] = []
    size = 0
    for path in matches[:maximum]:
        size += len(path)
        if size > OUTPUT_LIMIT:
            break
        selected.append(path)
    return result({"files": selected, "truncated": truncated or len(matches) > len(selected)})


def read_file(args: dict[str, Any], workspace: Workspace, cancel: Event) -> ToolResult:
    text, encoding = workspace.read_text(args["path"], cancel)
    start = args.get("start_line", 1)
    end = args.get("end_line", start + 199)
    if end < start or end - start >= 500:
        raise ValueError("读取范围需按顺序且不超过 500 行")
    lines = text.splitlines()
    output: list[str] = []
    size = 0
    clipped = False
    for index, line in enumerate(lines[start - 1 : end], start):
        item = f"{index}: {line}"
        if size + len(item) > OUTPUT_LIMIT:
            remaining = OUTPUT_LIMIT - size
            if remaining > 0:
                output.append(item[:remaining])
            clipped = True
            break
        output.append(item)
        size += len(item) + 1
    return result(
        {
            "path": args["path"],
            "content": "\n".join(output),
            "encoding": encoding,
            "total_lines": len(lines),
            "truncated": clipped or end < len(lines),
        }
    )


def search_code(args: dict[str, Any], workspace: Workspace, cancel: Event) -> ToolResult:
    pattern = args["pattern"]
    sensitive = args.get("case_sensitive", True)
    needle = pattern if sensitive else pattern.casefold()
    files, truncated = walk(workspace, ".", cancel)
    matches: list[dict[str, Any]] = []
    maximum, size, skipped = args.get("max_results", 50), 0, 0
    bytes_scanned = 0
    for path in files:
        if cancel.is_set():
            raise InterruptedError("任务已取消")
        if not fnmatch.fnmatchcase(path, args.get("glob", "*")):
            continue
        try:
            text, _ = workspace.read_text(path, cancel)
        except (OSError, ValueError):
            skipped += 1
            continue
        bytes_scanned += len(text)
        if bytes_scanned > 20_000_000:
            truncated = True
            break
        for index, line in enumerate(text.splitlines(), 1):
            haystack = line if sensitive else line.casefold()
            if needle in haystack:
                preview = line[:1000]
                size += len(preview) + len(path)
                if len(matches) >= maximum or size > OUTPUT_LIMIT:
                    return result({"matches": matches, "truncated": True, "skipped_files": skipped})
                matches.append({"path": path, "line": index, "text": preview})
    return result({"matches": matches, "truncated": truncated, "skipped_files": skipped})


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    path = {"type": "string", "minLength": 1, "maxLength": 4096}
    glob = {"type": "string", "minLength": 1, "maxLength": 256}
    maximum = {"type": "integer", "minimum": 1, "maximum": 300}
    schemas: tuple[
        tuple[
            str,
            str,
            dict[str, Any],
            list[str],
            Callable[[dict[str, Any], Workspace, Event], ToolResult],
        ],
        ...,
    ] = (
        (
            "list_files",
            "列出工作区文件，排除依赖、Git 元数据和敏感路径。",
            {"root": path, "glob": glob, "max_results": maximum},
            [],
            list_files,
        ),
        (
            "read_file",
            "读取工作区文本文件的指定行（1-based），输出包含行号。",
            {
                "path": path,
                "start_line": {"type": "integer", "minimum": 1, "maximum": 100000},
                "end_line": {"type": "integer", "minimum": 1, "maximum": 100000},
            },
            ["path"],
            read_file,
        ),
        (
            "search_code",
            "按字面文本搜索代码（非正则），返回文件、行号与限长上下文。",
            {
                "pattern": {"type": "string", "minLength": 1, "maxLength": 500},
                "glob": glob,
                "max_results": maximum,
                "case_sensitive": {"type": "boolean"},
            },
            ["pattern"],
            search_code,
        ),
    )
    for name, description, properties, required, execute in schemas:
        registry.register(
            ToolSpec(
                name,
                description,
                {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
                execute,
            )
        )
    return registry
