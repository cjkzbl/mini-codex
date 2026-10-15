"""Passive tool discovery; finding an executable never executes project code."""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from minicodex.backend.application.settings import ModelSettings


@dataclass(frozen=True)
class Dependency:
    name: str
    path: str | None
    hint: str


def discover(settings: ModelSettings, workspace: Path | None = None) -> list[Dependency]:
    python = settings.project_python.strip()
    if python:
        candidate = Path(python)
        python_path = (
            str(candidate.resolve())
            if candidate.is_absolute() and candidate.is_file()
            else shutil.which(python)
        )
    else:
        local = workspace / ".venv" / "Scripts" / "python.exe" if workspace else None
        python_path = str(local) if local and local.is_file() else shutil.which("python")
    if python_path and (
        Path(python_path).suffix.lower() not in {".exe", ".com"}
        or "windowsapps" in python_path.casefold()
    ):
        python_path = None
    git = shutil.which("git")
    docker = shutil.which("docker")
    return [
        Dependency(
            "项目 Python",
            python_path,
            "已找到（未执行）；pytest 等依赖仍需安装在该环境。"
            if python_path
            else "未找到。安装 Python 或创建项目 .venv，并在设置中选择 python.exe。",
        ),
        Dependency(
            "Git",
            git,
            "已找到（未执行）。"
            if git
            else "未找到。安装 Git for Windows 后重启应用，或检查 PATH。",
        ),
        Dependency(
            "Docker",
            docker,
            "已找到路径；Docker 实机功能继续暂缓。"
            if docker
            else "未安装；Docker 功能暂缓，不影响只读、补丁和 Windows 原生执行。",
        ),
        Dependency("运行环境", os.name, "客户端自带的运行时不等于目标项目 Python。"),
    ]
