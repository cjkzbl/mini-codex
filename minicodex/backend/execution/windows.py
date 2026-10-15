"""Windows handles: locked files and processes assigned to a Job before starting."""

import ctypes as c
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes as w
from pathlib import Path
from typing import Any, BinaryIO


class StartupInfo(c.Structure):
    _fields_ = [
        ("cb", w.DWORD),
        ("reserved", w.LPWSTR),
        ("desktop", w.LPWSTR),
        ("title", w.LPWSTR),
        ("x", w.DWORD),
        ("y", w.DWORD),
        ("xs", w.DWORD),
        ("ys", w.DWORD),
        ("xc", w.DWORD),
        ("yc", w.DWORD),
        ("fill", w.DWORD),
        ("flags", w.DWORD),
        ("show", w.WORD),
        ("reserved2size", w.WORD),
        ("reserved2", c.c_void_p),
        ("stdin", w.HANDLE),
        ("stdout", w.HANDLE),
        ("stderr", w.HANDLE),
    ]


class StartupInfoEx(c.Structure):
    _fields_ = [("info", StartupInfo), ("attributes", c.c_void_p)]


class ProcessInfo(c.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


class BasicLimit(c.Structure):
    _fields_ = [
        ("process_time", c.c_int64),
        ("job_time", c.c_int64),
        ("flags", w.DWORD),
        ("min_ws", c.c_size_t),
        ("max_ws", c.c_size_t),
        ("active_limit", w.DWORD),
        ("affinity", c.c_size_t),
        ("priority", w.DWORD),
        ("scheduling", w.DWORD),
    ]


class ExtendedLimit(c.Structure):
    _fields_ = [
        ("basic", BasicLimit),
        ("io", c.c_uint64 * 6),
        ("process_memory", c.c_size_t),
        ("job_memory", c.c_size_t),
        ("peak_process", c.c_size_t),
        ("peak_job", c.c_size_t),
    ]


class Accounting(c.Structure):
    _fields_ = [
        ("times", c.c_int64 * 4),
        ("faults", w.DWORD),
        ("total", w.DWORD),
        ("active", w.DWORD),
        ("terminated", w.DWORD),
    ]


def api() -> Any:
    if os.name != "nt":
        raise OSError("写入和原生执行目前仅支持 Windows")
    dll = c.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CloseHandle": ([w.HANDLE], w.BOOL),
        "GetCurrentProcess": ([], w.HANDLE),
        "DuplicateHandle": (
            [w.HANDLE, w.HANDLE, w.HANDLE, c.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD],
            w.BOOL,
        ),
        "CreateFileW": (
            [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE],
            w.HANDLE,
        ),
        "GetFinalPathNameByHandleW": ([w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD], w.DWORD),
        "GetFileInformationByHandleEx": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
        "SetFileInformationByHandle": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
        "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
        "SetInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
        "QueryInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
        "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
        "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
        "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
        "ResumeThread": ([w.HANDLE], w.DWORD),
        "GetExitCodeProcess": ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
        "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        "InitializeProcThreadAttributeList": (
            [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)],
            w.BOOL,
        ),
        "UpdateProcThreadAttribute": (
            [c.c_void_p, w.DWORD, c.c_size_t, c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p],
            w.BOOL,
        ),
        "DeleteProcThreadAttributeList": ([c.c_void_p], None),
        "CreateProcessW": (
            [
                w.LPCWSTR,
                w.LPWSTR,
                c.c_void_p,
                c.c_void_p,
                w.BOOL,
                w.DWORD,
                c.c_void_p,
                w.LPCWSTR,
                c.c_void_p,
                c.c_void_p,
            ],
            w.BOOL,
        ),
    }
    for name, (args, result) in signatures.items():
        function = getattr(dll, name)
        function.argtypes, function.restype = args, result
    return dll


def checked(value: Any) -> Any:
    if not value:
        raise c.WinError(c.get_last_error())
    return value


def final_path(dll: Any, handle: int) -> Path:
    buffer = c.create_unicode_buffer(32768)
    length = dll.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not 0 < length < len(buffer):
        raise PermissionError("无法验证实际文件句柄")
    return Path(buffer.value.removeprefix("\\\\?\\"))


@contextmanager
def locked_handle(path: Path, *, directory: bool = False, create: bool = False) -> Iterator[int]:
    dll = api()
    access = 0x80000000 if directory else 0xC0010000  # read/write/delete for files
    flags = 0x00200000 | (0x02000000 if directory else 0)  # open reparse + backup semantics
    handle = dll.CreateFileW(str(path), access, 1, None, 1 if create else 3, flags, None)
    if handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    try:
        attributes = (w.DWORD * 2)()
        checked(dll.GetFileInformationByHandleEx(handle, 9, attributes, c.sizeof(attributes)))
        if attributes[0] & 0x400:
            raise PermissionError("写路径包含 reparse point")
        yield handle
    finally:
        dll.CloseHandle(handle)


@contextmanager
def locked_file(path: Path, *, create: bool = False) -> Iterator[BinaryIO]:
    import msvcrt

    with locked_handle(path, create=create) as handle:
        dll = api()
        duplicate = w.HANDLE()
        process = dll.GetCurrentProcess()
        checked(dll.DuplicateHandle(process, handle, process, c.byref(duplicate), 0, False, 2))
        assert duplicate.value is not None
        fd = msvcrt.open_osfhandle(duplicate.value, os.O_BINARY)
        with os.fdopen(fd, "r+b", buffering=0) as stream:
            yield stream


def mark_deleted(stream: BinaryIO, delete: bool) -> None:
    import msvcrt

    value = w.BOOL(delete)
    checked(
        api().SetFileInformationByHandle(
            msvcrt.get_osfhandle(stream.fileno()), 4, c.byref(value), c.sizeof(value)
        )
    )


class WindowsProcess:
    def __init__(self, argv: list[str], cwd: Path, environment: dict[str, str]) -> None:
        import msvcrt

        self.dll = api()
        self.job = checked(self.dll.CreateJobObjectW(None, None))
        self.info = ProcessInfo()
        self.readers: list[BinaryIO] = []
        limits = ExtendedLimit()
        limits.basic.flags = 0x2000 | 0x8  # kill on close; max active processes
        limits.basic.active_limit = 64
        pipes: list[int] = []
        attribute_buffer = None
        initialized = False
        try:
            checked(
                self.dll.SetInformationJobObject(self.job, 9, c.byref(limits), c.sizeof(limits))
            )
            outputs = []
            for _ in range(2):
                read_fd, write_fd = os.pipe()
                pipes.append(write_fd)
                os.set_handle_inheritable(msvcrt.get_osfhandle(write_fd), True)
                self.readers.append(os.fdopen(read_fd, "rb", buffering=0))
                outputs.append(msvcrt.get_osfhandle(write_fd))
            null_fd = os.open(os.devnull, os.O_RDONLY)
            pipes.append(null_fd)
            null_handle = msvcrt.get_osfhandle(null_fd)
            os.set_handle_inheritable(null_handle, True)
            handles = (w.HANDLE * 3)(null_handle, *outputs)
            size = c.c_size_t()
            self.dll.InitializeProcThreadAttributeList(None, 1, 0, c.byref(size))
            attribute_buffer = c.create_string_buffer(size.value)
            checked(
                self.dll.InitializeProcThreadAttributeList(attribute_buffer, 1, 0, c.byref(size))
            )
            initialized = True
            checked(
                self.dll.UpdateProcThreadAttribute(
                    attribute_buffer, 0, 0x20002, handles, c.sizeof(handles), None, None
                )
            )
            startup = StartupInfoEx()
            startup.info.cb = c.sizeof(startup)
            startup.info.flags = 0x100 | 0x1  # standard handles + hidden window
            startup.info.stdin, startup.info.stdout, startup.info.stderr = handles
            startup.attributes = c.cast(attribute_buffer, c.c_void_p)
            env = c.create_unicode_buffer(
                "\0".join(f"{k}={v}" for k, v in sorted(environment.items())) + "\0\0"
            )
            command = c.create_unicode_buffer(subprocess.list2cmdline(argv))
            checked(
                self.dll.CreateProcessW(
                    argv[0],
                    command,
                    None,
                    None,
                    True,
                    0x4 | 0x8000000 | 0x80000 | 0x400,
                    env,
                    str(cwd),
                    c.byref(startup),
                    c.byref(self.info),
                )
            )
            checked(self.dll.AssignProcessToJobObject(self.job, self.info.process))
            if self.dll.ResumeThread(self.info.thread) == 0xFFFFFFFF:
                raise c.WinError(c.get_last_error())
        except BaseException:
            self.close()
            for reader in self.readers:
                reader.close()
            raise
        finally:
            for fd in pipes:
                os.close(fd)
            if initialized:
                self.dll.DeleteProcThreadAttributeList(attribute_buffer)

    def poll(self) -> int | None:
        if self.dll.WaitForSingleObject(self.info.process, 0) == 0x102:
            return None
        code = w.DWORD()
        checked(self.dll.GetExitCodeProcess(self.info.process, c.byref(code)))
        return int(code.value)

    def terminate(self) -> None:
        checked(self.dll.TerminateJobObject(self.job, 1))

    def active_count(self) -> int:
        info = Accounting()
        checked(
            self.dll.QueryInformationJobObject(self.job, 1, c.byref(info), c.sizeof(info), None)
        )
        return int(info.active)

    def close(self) -> None:
        if self.info.process:
            self.dll.TerminateProcess(self.info.process, 1)
        if self.job:
            self.dll.TerminateJobObject(self.job, 1)
            self.dll.CloseHandle(self.job)
            self.job = None
        for name in ("thread", "process"):
            handle = getattr(self.info, name)
            if handle:
                self.dll.CloseHandle(handle)
                setattr(self.info, name, None)
