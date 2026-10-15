import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from threading import Event
from typing import Any

from minicodex.backend.core.context import excerpt
from minicodex.backend.core.models import ToolResult
from minicodex.backend.core.redaction import redact
from minicodex.backend.execution.workspace import Workspace
from minicodex.backend.tools.registry import ToolSpec


class ObservationArchive:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory
        self.memory: dict[tuple[str, str], str] = {}
        self.total = 0
        self.saved: set[tuple[str, str]] = set()

    def bound(self, text: str, thread_id: str, secret: str) -> tuple[str, str | None]:
        text = redact(text, secret)
        if len(text.encode("utf-8")) <= 12_000:
            return text, None
        data = text.encode("utf-8")
        reference = hashlib.sha256(data).hexdigest()
        already_saved = (thread_id, reference) in self.saved
        stored = len(data) <= 16 * 1024 * 1024 and self.total + len(data) <= 64 * 1024 * 1024
        stored = stored or already_saved
        if stored:
            if self.directory:
                root = self.directory / thread_id
                root.mkdir(parents=True, exist_ok=True)
                if root.is_symlink() or getattr(root.lstat(), "st_file_attributes", 0) & 0x400:
                    raise PermissionError("归档目录不可为链接或 reparse point")
                path = root / (reference + ".txt")
                if not path.exists():
                    temporary: Path | None = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=root, delete=False) as stream:
                            temporary = Path(stream.name)
                            stream.write(data)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, path)
                    finally:
                        if temporary:
                            temporary.unlink(missing_ok=True)
            else:
                self.memory[thread_id, reference] = text
            if not already_saved:
                self.total += len(data)
                self.saved.add((thread_id, reference))
        return json.dumps(
            {
                "truncated": True,
                "original_bytes": len(data),
                "artifact_id": reference if stored else None,
                "content": excerpt(text, 7000),
                "readback": "用 read_observation 分页回读"
                if stored
                else "归档超过大小预算，请重新精确查询",
            },
            ensure_ascii=False,
        ), reference if stored else None

    def tool(self, thread_id: str) -> ToolSpec:
        def read(args: dict[str, Any], workspace: Workspace, cancel: Event) -> ToolResult:
            reference = args["artifact_id"]
            if cancel.is_set():
                raise InterruptedError("任务已取消")
            if not re.fullmatch(r"[a-f0-9]{64}", reference):
                raise ValueError("无效归档 ID")
            if self.directory:
                root = self.directory / thread_id
                if root.is_symlink() or getattr(root.lstat(), "st_file_attributes", 0) & 0x400:
                    raise PermissionError("归档目录不可为链接或 reparse point")
                text, _ = Workspace(root).read_text(
                    reference + ".txt", cancel, max_bytes=16 * 1024 * 1024
                )
                if hashlib.sha256(text.encode("utf-8")).hexdigest() != reference:
                    raise ValueError("归档内容校验失败，不能作为原始结果返回")
            else:
                text = self.memory.get((thread_id, reference), "")
                if not text:
                    raise ValueError("当前会话无此归档")
            start = args.get("offset", 0)
            end = start + args.get("limit", 2000)
            return ToolResult(
                "pending",
                True,
                json.dumps(
                    {
                        "artifact_id": reference,
                        "content": text[start:end],
                        "offset": start,
                        "next_offset": end if end < len(text) else None,
                        "total_chars": len(text),
                    },
                    ensure_ascii=False,
                ),
            )

        return ToolSpec(
            "read_observation",
            "分页回读当前会话被截断的工具原始结果，只读归档 ID。",
            {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 3000},
                },
                "required": ["artifact_id"],
                "additionalProperties": False,
            },
            read,
        )
