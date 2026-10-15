import difflib
import hashlib
import os
import re
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import BinaryIO

from minicodex.backend.execution.windows import (
    api,
    final_path,
    locked_file,
    locked_handle,
    mark_deleted,
)
from minicodex.backend.execution.workspace import Workspace, decode_text

WRITE_LOCK = Lock()
HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*")


@dataclass(frozen=True)
class Change:
    path: str
    before: bytes | None
    after: bytes | None


@dataclass(frozen=True)
class PatchPlan:
    changes: tuple[Change, ...]
    diff: str
    fingerprint: str


def prepare_patch(patch: str, workspace: Workspace, cancel: Event) -> PatchPlan:
    if len(patch) > 100_000 or "\x00" in patch:
        raise ValueError("补丁超过大小限制或含 NUL")
    lines = patch.splitlines()
    index = 0
    changes: list[Change] = []
    previews: list[str] = []
    seen: set[str] = set()
    while index < len(lines):
        if cancel.is_set():
            raise InterruptedError("任务已取消")
        if (
            not lines[index].startswith("--- ")
            or index + 1 >= len(lines)
            or not lines[index + 1].startswith("+++ ")
        ):
            raise ValueError("请提供标准 unified diff，包含 --- / +++ 和 @@ 行")
        old_name = lines[index][4:].split("\t", 1)[0]
        new_name = lines[index + 1][4:].split("\t", 1)[0]
        old = old_name.removeprefix("a/") if old_name != "/dev/null" else None
        new = new_name.removeprefix("b/") if new_name != "/dev/null" else None
        if (old is None and new is None) or (old and new and old != new):
            raise ValueError("不支持移动或重命名补丁")
        path = old or new or ""
        target = workspace.write_target(path)
        identity = str(target).casefold()
        if identity in seen or len(changes) >= 20:
            raise ValueError("同一文件不能重复出现在补丁中，最多修改 20 个文件")
        seen.add(identity)
        if old is None:
            if target.exists():
                raise ValueError("新增文件已存在")
            before, text, encoding = None, "", "utf-8"
        else:
            before = workspace.read_bytes(path, cancel)
            text, encoding = decode_text(before)
        stripped_crlf = text.replace("\r\n", "")
        if "\r" in stripped_crlf or ("\r\n" in text and "\n" in stripped_crlf):
            raise ValueError("混合换行符或单独 CR 暂不支持补丁，避免改变原文件格式")
        newline = "\r\n" if "\r\n" in text else "\n"
        logical = text.splitlines()
        result: list[str] = []
        cursor = 0
        index += 2
        hunks = 0
        final_newline = text.endswith(("\n", "\r")) if text else True
        while index < len(lines) and not lines[index].startswith("--- "):
            match = HUNK.fullmatch(lines[index])
            if not match:
                raise ValueError("无效的补丁 hunk")
            start, old_count, new_start, new_count = (
                int(match[1]),
                int(match[2] or 1),
                int(match[3]),
                int(match[4] or 1),
            )
            position = start - 1 if old_count else start
            if position < cursor or position > len(logical):
                raise ValueError("补丁行号重叠或越界")
            result.extend(logical[cursor:position])
            if (new_start - 1 if new_count else new_start) != len(result):
                raise ValueError("补丁新增行号不一致")
            cursor = position
            consumed = produced = 0
            index += 1
            previous = ""
            while index < len(lines):
                line = lines[index]
                if line == "\\ No newline at end of file":
                    if previous in {"+", " "}:
                        final_newline = False
                    if not previous:
                        raise ValueError("无效换行标记")
                    index += 1
                    continue
                if consumed == old_count and produced == new_count:
                    break
                if not line or line[0] not in {" ", "+", "-"}:
                    raise ValueError("补丁行缺少前缀")
                prefix, value = line[0], line[1:]
                if prefix in {" ", "-"}:
                    if cursor >= len(logical) or logical[cursor] != value:
                        raise ValueError("补丁原文不匹配，请重新读取文件")
                    cursor += 1
                    consumed += 1
                if prefix in {" ", "+"}:
                    result.append(value)
                    produced += 1
                    if prefix == "+" and cursor >= len(logical):
                        final_newline = True
                previous = prefix
                index += 1
                if consumed > old_count or produced > new_count:
                    raise ValueError("补丁 hunk 行数不匹配")
            if consumed != old_count or produced != new_count:
                raise ValueError("补丁 hunk 未完整结束")
            hunks += 1
        if not hunks:
            raise ValueError("补丁没有 hunk")
        result.extend(logical[cursor:])
        changed = newline.join(result) + (newline if result and final_newline else "")
        if new is None:
            if result:
                raise ValueError("删除文件补丁未移除全部内容")
            after = None
        else:
            after = changed.encode(encoding)
            if encoding == "utf-16-le":
                after = b"\xff\xfe" + after
            elif encoding == "utf-16-be":
                after = b"\xfe\xff" + after
            if len(after) > 1_048_576:
                raise ValueError("补丁生成文件超过 1 MiB")
        if before == after:
            raise ValueError("补丁没有实际变化")
        changes.append(Change(path, before, after))
        previews.extend(
            difflib.unified_diff(
                text.splitlines(keepends=True),
                changed.splitlines(keepends=True),
                fromfile=old_name,
                tofile=new_name,
            )
        )
    if not changes:
        raise ValueError("补丁为空")
    digest = hashlib.sha256()
    for change in changes:
        digest.update(change.path.encode())
        for data in (change.before, change.after):
            digest.update(b"missing" if data is None else hashlib.sha256(data).digest())
    diff = "".join(previews)
    if len(diff) > 200_000:
        raise ValueError("diff 预览超过 200000 字符，请提交更小的补丁")
    return PatchPlan(tuple(changes), diff, digest.hexdigest())


def apply_plan(plan: PatchPlan, workspace: Workspace, cancel: Event) -> list[str]:
    import msvcrt

    if os.name != "nt":
        raise OSError("当前补丁写入仅在 Windows 验收")
    with WRITE_LOCK, ExitStack() as stack:
        directories: set[Path] = {workspace.root}
        for change in plan.changes:
            parent = workspace.write_target(change.path).parent
            while parent != workspace.root:
                directories.add(parent)
                parent = parent.parent
        dll = api()
        for directory in sorted(directories, key=lambda p: len(p.parts)):
            handle = stack.enter_context(locked_handle(directory, directory=True))
            workspace.check_resolved(final_path(dll, handle))
        streams: list[tuple[Change, BinaryIO]] = []
        modified: list[tuple[Change, BinaryIO]] = []
        try:
            for change in plan.changes:
                if cancel.is_set():
                    raise InterruptedError("任务已取消")
                target = workspace.write_target(change.path)
                stream = stack.enter_context(locked_file(target, create=change.before is None))
                streams.append((change, stream))
                workspace.check_resolved(final_path(dll, msvcrt.get_osfhandle(stream.fileno())))
                if os.fstat(stream.fileno()).st_nlink != 1:
                    raise PermissionError("不允许修改硬链接文件")
                current = stream.read(1_048_577)
                if (change.before is None and current) or (
                    change.before is not None and current != change.before
                ):
                    raise ValueError("审批后文件已变化，补丁失效；请重新读取")
            for change, stream in streams:
                if cancel.is_set():
                    raise InterruptedError("任务已取消")
                modified.append((change, stream))
                if change.after is None:
                    mark_deleted(stream, True)
                else:
                    stream.seek(0)
                    stream.write(change.after)
                    stream.truncate()
                    os.fsync(stream.fileno())
            if cancel.is_set():
                raise InterruptedError("任务已取消")
        except BaseException:
            failed = False
            for change, stream in streams:
                try:
                    if change.before is None:
                        mark_deleted(stream, True)
                    elif (change, stream) in modified:
                        mark_deleted(stream, False)
                        stream.seek(0)
                        stream.write(change.before)
                        stream.truncate()
                        os.fsync(stream.fileno())
                except OSError:
                    failed = True
            if failed:
                raise RuntimeError("补丁失败且回滚结果未知，请检查 diff；禁止自动重放") from None
            raise
    return [change.path for change in plan.changes]
