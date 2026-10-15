from pathlib import PurePosixPath
from threading import Event

from minicodex.backend.execution.workspace import Workspace


def load_instructions(workspace: Workspace, paths: set[str], cancel: Event) -> str:
    directories = {"."}
    for raw in sorted(paths)[:40]:
        try:
            resolved = workspace.resolve(raw)
            parent_path = resolved if resolved.is_dir() else resolved.parent
            relative = PurePosixPath(parent_path.relative_to(workspace.root).as_posix())
            for parent in [*reversed(relative.parents), relative]:
                if len(parent.parts) <= 12:
                    directories.add(str(parent))
        except (OSError, ValueError):
            continue
    sections: list[str] = []
    total = 0
    for directory in sorted(directories, key=lambda p: (len(PurePosixPath(p).parts), p)):
        path = str(PurePosixPath(directory) / "AGENTS.md")
        try:
            text, _ = workspace.read_text(path, cancel, max_bytes=16_384)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as error:
            text = f"[说明未加载：{type(error).__name__}]"
        section = f"\n--- 工作区文件 {path} ---\n{text}\n"
        total += len(section.encode("utf-8"))
        if total > 32_768:
            sections.append("[其他项目说明超过加载预算，未加载]")
            break
        sections.append(section)
    return "".join(sections)
