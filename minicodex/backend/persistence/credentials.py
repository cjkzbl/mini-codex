"""Current-user Windows Generic Credentials. No plaintext fallback."""

import ctypes
import os
from ctypes import wintypes
from typing import Any, Protocol


class CredentialBackend(Protocol):
    def read(self, target: str) -> str | None: ...
    def write(self, target: str, secret: str) -> None: ...
    def delete(self, target: str) -> None: ...
    def targets(self, prefix: str) -> list[str]: ...


class Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentials:
    def __init__(self) -> None:
        self._api: Any = None

    def api(self) -> Any:
        if os.name != "nt":
            raise OSError("此系统不支持 Windows 凭据管理器；密钥仅可保留在内存")
        if self._api is None:
            dll = ctypes.WinDLL("advapi32", use_last_error=True)
            pointer = ctypes.POINTER(Credential)
            dll.CredWriteW.argtypes = [pointer, wintypes.DWORD]
            dll.CredWriteW.restype = wintypes.BOOL
            dll.CredReadW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(pointer),
            ]
            dll.CredReadW.restype = wintypes.BOOL
            dll.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            dll.CredDeleteW.restype = wintypes.BOOL
            dll.CredEnumerateW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(ctypes.POINTER(pointer)),
            ]
            dll.CredEnumerateW.restype = wintypes.BOOL
            dll.CredFree.argtypes = [ctypes.c_void_p]
            dll.CredFree.restype = None
            self._api = dll
        return self._api

    @staticmethod
    def error() -> OSError:
        return OSError(
            f"Windows 凭据操作失败（错误码 {ctypes.get_last_error()}），未回退为明文保存"
        )

    def read(self, target: str) -> str | None:
        api = self.api()
        value = ctypes.POINTER(Credential)()
        if not api.CredReadW(target, 1, 0, ctypes.byref(value)):
            if ctypes.get_last_error() == 1168:
                return None
            raise self.error()
        try:
            return ctypes.string_at(
                value.contents.CredentialBlob, value.contents.CredentialBlobSize
            ).decode("utf-8")
        finally:
            api.CredFree(value)

    def write(self, target: str, secret: str) -> None:
        data = secret.encode("utf-8")
        if not data or len(data) > 2560:
            raise ValueError("保存的密钥必须为 1～2560 UTF-8 字节")
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        value = Credential(
            Type=1,
            TargetName=target,
            Comment="MiniCodex API key",
            CredentialBlobSize=len(data),
            CredentialBlob=buffer,
            Persist=2,
            UserName="MiniCodex",
        )
        try:
            if not self.api().CredWriteW(ctypes.byref(value), 0):
                raise self.error()
        finally:
            ctypes.memset(buffer, 0, len(data))

    def delete(self, target: str) -> None:
        if not self.api().CredDeleteW(target, 1, 0) and ctypes.get_last_error() != 1168:
            raise self.error()

    def targets(self, prefix: str) -> list[str]:
        api = self.api()
        count = wintypes.DWORD()
        values = ctypes.POINTER(ctypes.POINTER(Credential))()
        if not api.CredEnumerateW(prefix + "*", 0, ctypes.byref(count), ctypes.byref(values)):
            if ctypes.get_last_error() == 1168:
                return []
            raise self.error()
        try:
            return [
                values[i].contents.TargetName
                for i in range(count.value)
                if values[i].contents.TargetName.startswith(prefix)
            ]
        finally:
            api.CredFree(values)
