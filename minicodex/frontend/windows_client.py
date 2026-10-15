import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any

APP_MUTEX = r"Local\MiniCodexDesktop-v1"
MAINTENANCE_MUTEX = r"Local\MiniCodexMaintenance-v1"


class NamedMutex:
    def __init__(self, name: str) -> None:
        api: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        api.CreateMutexW.restype = wintypes.HANDLE
        api.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        api.OpenMutexW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        self.api = api
        self.handle = api.CreateMutexW(None, False, name)
        self.existed = ctypes.get_last_error() == 183
        if not self.handle:
            raise OSError("无法创建应用状态互斥对象")

    def exists(self, name: str) -> bool:
        value = self.api.OpenMutexW(0x00100000, False, name)
        if value:
            self.api.CloseHandle(value)
            return True
        return False

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class ShellFileOperation(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def validate_data_removal(directory: Path, expected: Path) -> Path:
    path = directory.absolute()
    if path != expected.absolute() or path.name != "MiniCodex":
        raise ValueError("只允许清理当前用户的 MiniCodex 默认数据目录")
    # Validate before resolving: a link must not turn an apparently safe root into another path.
    for parent in (path, *path.parents):
        if parent.exists() and (
            parent.is_symlink() or getattr(parent.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("数据路径包含链接或 reparse point，未执行清理")
    path = path.resolve(strict=True)
    for folder, directories, files in os.walk(path, followlinks=False):
        for name in [*directories, *files]:
            child = Path(folder) / name
            if child.is_symlink() or getattr(child.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("数据中含链接或 reparse point，未执行清理")
            if not child.absolute().is_relative_to(path):
                raise ValueError("清理路径越界")
    identity = json.loads((path / "data_identity.json").read_text("utf-8"))
    if identity.get("application") != "MiniCodex" or identity.get("data_version") != 1:
        raise ValueError("此目录不具备 MiniCodex 数据标记，未执行清理")
    return path


def recycle_data(directory: Path, expected: Path) -> None:
    path = validate_data_removal(directory, expected)
    shell: Any = ctypes.WinDLL("shell32", use_last_error=True)
    shell.SHFileOperationW.argtypes = [ctypes.POINTER(ShellFileOperation)]
    shell.SHFileOperationW.restype = ctypes.c_int
    request = ShellFileOperation(
        wFunc=3, pFrom=str(path) + "\0\0", fFlags=0x40 | 0x10 | 0x04 | 0x400
    )
    if shell.SHFileOperationW(ctypes.byref(request)) != 0 or request.fAnyOperationsAborted:
        raise OSError("数据移至回收站未完成，未确认清理成功")


def purge_default_data() -> None:
    from PySide6.QtCore import QLockFile

    from minicodex.backend.persistence.configuration import ConfigurationStore
    from minicodex.backend.persistence.paths import default_data_directory

    guard = NamedMutex(MAINTENANCE_MUTEX)
    try:
        if guard.existed or guard.exists(APP_MUTEX):
            raise RuntimeError("请先关闭所有 MiniCodex 窗口再清理数据")
        directory = default_data_directory()
        if not directory.exists():
            return
        validate_data_removal(directory, directory)
        data_lock = QLockFile(str(directory / "desktop.lock"))
        data_lock.setStaleLockTime(0)
        if not data_lock.tryLock(0):
            raise RuntimeError("数据目录仍被其他进程使用，未清理")
        try:
            config = ConfigurationStore(directory)
            for target in config.credentials.targets(config.prefix):
                config.credentials.delete(target)
        finally:
            data_lock.unlock()
        # Maintenance mutex blocks new M4 desktop processes after releasing the in-folder lock.
        recycle_data(directory, directory)
    finally:
        guard.close()
