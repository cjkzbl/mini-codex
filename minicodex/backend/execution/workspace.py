import ctypes
import os
import stat
from pathlib import Path, PureWindowsPath
from threading import Event

EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".ssh",
        ".aws",
        ".azure",
    }
)


def protected(parts: tuple[str, ...]) -> bool:
    for part in parts:
        name = part.casefold()
        if (
            name in EXCLUDED_DIRS
            or name.startswith(".env")
            or name in {"id_rsa", "id_ed25519", "credentials"}
            or name.endswith((".pem", ".key", ".p12", ".pfx"))
        ):
            return True
    return False


class Workspace:
    """One selected local root; every file open verifies the actual handle path."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        if not self.root.is_dir() or str(self.root).startswith(("\\\\", "//")):
            raise ValueError("仅支持本地工作区目录")

    def resolve(self, relative: str, *, directory: bool = False) -> Path:
        if not relative or "\x00" in relative or ":" in relative:
            raise ValueError("无效路径或 NTFS alternate data stream")
        win = PureWindowsPath(relative)
        if win.is_absolute() or win.drive or relative.startswith(("/", "\\")):
            raise ValueError("工具路径必须相对于工作区")
        parts = tuple(p for p in relative.replace("\\", "/").split("/") if p not in {"", "."})
        if ".." in parts or protected(parts):
            raise PermissionError("路径越界或受保护")
        candidate = self.root
        for part in parts:
            candidate = candidate / part
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise PermissionError("不允许读取符号链接或 reparse point")
        resolved = candidate.resolve(strict=True)
        self.check_resolved(resolved)
        if directory and not resolved.is_dir():
            raise ValueError("目标不是文件夹")
        return resolved

    def check_resolved(self, path: Path) -> None:
        try:
            parts = path.relative_to(self.root).parts
        except ValueError:
            raise PermissionError("目标超出工作区") from None
        if protected(parts):
            raise PermissionError("目标为受保护文件")

    def read_text(
        self, relative: str, cancel: Event, *, max_bytes: int = 1_048_576
    ) -> tuple[str, str]:
        data = self.read_bytes(relative, cancel, max_bytes=max_bytes)
        return decode_text(data)

    def read_bytes(self, relative: str, cancel: Event, *, max_bytes: int = 1_048_576) -> bytes:
        if cancel.is_set():
            raise InterruptedError("任务已取消")
        path = self.resolve(relative)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("只允许读取普通文件")
            if os.name == "nt":
                import msvcrt

                api = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
                api.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
                api.restype = ctypes.c_ulong
                buffer = ctypes.create_unicode_buffer(32768)
                length = api(msvcrt.get_osfhandle(handle.fileno()), buffer, len(buffer), 0)
                if not 0 < length < len(buffer):
                    raise PermissionError("无法验证实际文件路径")
                final = buffer.value
                if final.startswith("\\\\?\\"):
                    final = final[4:]
                self.check_resolved(Path(final))
            else:
                self.check_resolved(path.resolve(strict=True))
            if info.st_size > max_bytes:
                raise ValueError("文件超过 1 MiB 读取上限")
            data = handle.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("文件读取超过上限")
        if cancel.is_set():
            raise InterruptedError("任务已取消")
        return data

    def write_target(self, relative: str) -> Path:
        if not relative or "\x00" in relative or ":" in relative:
            raise ValueError("无效写路径")
        win = PureWindowsPath(relative)
        if win.is_absolute() or win.drive or relative.startswith(("/", "\\")):
            raise PermissionError("写路径必须相对于工作区")
        parts = tuple(relative.replace("\\", "/").split("/"))
        if any(p in {"", ".", ".."} or p.endswith((".", " ")) for p in parts) or protected(parts):
            raise PermissionError("写路径越界、歧义或受保护")
        reserved = {
            "con",
            "prn",
            "aux",
            "nul",
            *(f"com{i}" for i in range(1, 10)),
            *(f"lpt{i}" for i in range(1, 10)),
        }
        if any(p.split(".")[0].casefold() in reserved for p in parts):
            raise PermissionError("不允许写 Windows 设备名")
        parent = self.resolve("/".join(parts[:-1]) or ".", directory=True)
        target = parent / parts[-1]
        if target.exists() or target.is_symlink():
            target = self.resolve(relative)
            if not target.is_file():
                raise ValueError("补丁目标必须是普通文件")
        return target


def decode_text(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le"), "utf-16-le"
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be"), "utf-16-be"
    if b"\x00" in data:
        raise ValueError("二进制文件不支持文本读取")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError("无法识别文本编码")
