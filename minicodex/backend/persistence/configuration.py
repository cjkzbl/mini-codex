import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.persistence.credentials import CredentialBackend, WindowsCredentials


class ConfigurationStore:
    VERSION = 2

    def __init__(self, directory: Path, credentials: CredentialBackend | None = None) -> None:
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        identity = self.directory / "data_identity.json"
        if not identity.exists():
            with identity.open("x", encoding="utf-8") as marker:
                json.dump({"application": "MiniCodex", "data_version": 1}, marker)
        self.path = self.directory / "settings.json"
        self.credentials = credentials or WindowsCredentials()
        scope = hashlib.sha256(str(self.directory).casefold().encode()).hexdigest()[:24]
        self.prefix = f"MiniCodex/{scope}/"
        self.record: dict[str, Any] = {
            "version": self.VERSION,
            "settings": {},
            "credential_ref": None,
            "recent_projects": [],
        }
        self.remember_key = False
        self.warning = ""
        self.readonly = False

    def endpoint_prefix(self, settings: ModelSettings) -> str:
        url = urlsplit(settings.base_url)
        normalized = urlunsplit(
            (url.scheme.lower(), url.netloc.lower(), url.path.rstrip("/"), "", "")
        )
        return self.prefix + hashlib.sha256(normalized.encode()).hexdigest()[:24] + "/"

    def _write(self, record: dict[str, Any]) -> None:
        if self.readonly:
            raise ValueError("配置格式无法安全覆盖，请先在设置中重置配置")
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(record, ensure_ascii=False, indent=2).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        self.record = record

    def load(self) -> ModelSettings:
        if not self.path.exists():
            return ModelSettings()
        try:
            if (
                self.path.is_symlink()
                or getattr(self.path.lstat(), "st_file_attributes", 0) & 0x400
            ):
                raise ValueError("配置不可为链接")
            if self.path.stat().st_size > 128 * 1024:
                raise ValueError("配置文件过大")
            record = json.loads(self.path.read_text("utf-8"))
            version = record.get("version", 1)
            if version not in {1, self.VERSION}:
                raise ValueError("配置版本较新")
            raw = record.get("settings", record if version == 1 else {})
            allowed = {f.name for f in fields(ModelSettings)}
            defaults = asdict(ModelSettings())
            for key, value in raw.items():
                if key not in allowed:
                    continue
                expected_type = type(defaults[key])
                if expected_type is float:
                    valid = type(value) in {int, float}
                else:
                    valid = type(value) is expected_type
                if not valid:
                    raise ValueError("配置字段类型不正确")
            projects = record.get("recent_projects", [])
            if not isinstance(projects, list) or not all(isinstance(p, str) for p in projects):
                raise ValueError("最近项目配置不正确")
            settings = ModelSettings(**{k: v for k, v in raw.items() if k in allowed})
            if version == 1 or "api_key" in raw or "api_key" in record:
                if not settings.api_key and isinstance(record.get("api_key"), str):
                    settings = replace(settings, api_key=record["api_key"])
                # Preserve a legacy key for this process only; never create a plaintext backup.
                self.record = {
                    "version": self.VERSION,
                    "settings": {},
                    "credential_ref": None,
                    "recent_projects": record.get("recent_projects", []),
                }
                self.save(settings, remember=False)
                self.warning = "旧配置已迁移并移除明文 Key；需勾选 Windows 凭据保存才能跨重启使用。"
                return settings
            self.record = {
                key: record[key]
                for key in (
                    "version",
                    "settings",
                    "credential_ref",
                    "recent_projects",
                    "window_size",
                )
                if key in record
            }
            target = record.get("credential_ref")
            if target:
                expected = self.endpoint_prefix(settings)
                if not isinstance(target, str) or not re.fullmatch(
                    re.escape(expected) + r"[a-f0-9]{32}", target
                ):
                    raise ValueError("凭据引用与当前服务地址不匹配")
                self.remember_key = True
                try:
                    key = self.credentials.read(target)
                    if key is None:
                        self.warning = "已保存的凭据不存在，请重新输入 API Key。"
                    settings = replace(settings, api_key=key or "")
                except (OSError, UnicodeError, ValueError):
                    self.warning = "Windows 凭据读取失败，请重新输入 API Key；配置没有明文密钥。"
            return settings
        except (OSError, ValueError, TypeError, AttributeError):
            self.readonly = True
            self.warning = (
                "配置损坏或版本不兼容，已使用离线/只读默认值，原文件保留；可在设置中重置。"
            )
            return ModelSettings()

    def save(self, settings: ModelSettings, *, remember: bool) -> None:
        target: str | None = None
        if remember and settings.api_key:
            target = self.endpoint_prefix(settings) + uuid4().hex
            self.credentials.write(target, settings.api_key)
        public = asdict(settings)
        public.pop("api_key")
        record = {
            **self.record,
            "version": self.VERSION,
            "settings": public,
            "credential_ref": target,
        }
        try:
            self._write(record)
        except BaseException:
            if target:
                self.credentials.delete(target)
            raise
        self.remember_key = target is not None
        self.warning = ""
        try:
            for stale in self.credentials.targets(self.prefix):
                if stale != target:
                    self.credentials.delete(stale)
        except OSError:
            self.warning = "配置已保存；旧凭据清理未完成，可点击“忘记保存的密钥”重试。"

    def forget(self) -> None:
        for target in self.credentials.targets(self.prefix):
            self.credentials.delete(target)
        self._write({**self.record, "credential_ref": None})
        self.remember_key = False

    def reset(self) -> None:
        # Explicit UI action: resets only configuration; conversation DB is untouched.
        self.readonly = False
        self.record = {
            "version": self.VERSION,
            "settings": {},
            "credential_ref": None,
            "recent_projects": [],
        }
        self.save(ModelSettings(), remember=False)

    def remember_project(self, path: Path) -> None:
        if self.readonly:
            return
        projects = [
            str(path),
            *(
                p
                for p in self.record.get("recent_projects", [])
                if isinstance(p, str) and p != str(path)
            ),
        ][:10]
        self._write({**self.record, "recent_projects": projects})

    def save_window(self, width: int, height: int) -> None:
        if not self.readonly:
            self._write({**self.record, "window_size": [width, height]})
