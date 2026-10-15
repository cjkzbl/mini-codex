import json
from dataclasses import replace
from uuid import uuid4

import pytest

from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.persistence.configuration import ConfigurationStore
from minicodex.backend.persistence.credentials import WindowsCredentials


class MemoryCredentials:
    def __init__(self):
        self.values = {}

    def read(self, target):
        return self.values.get(target)

    def write(self, target, secret):
        self.values[target] = secret

    def delete(self, target):
        self.values.pop(target, None)

    def targets(self, prefix):
        return [key for key in self.values if key.startswith(prefix)]


@pytest.mark.parametrize("field", ["model", "base_url", "project_python", "docker_image"])
def test_key_cannot_be_persisted_by_pasting_into_public_fields(field):
    secret = "synthetic-private-m4-value"
    with pytest.raises(ValueError, match="密钥栏"):
        ModelSettings(api_key=secret, **{field: "prefix-" + secret})


@pytest.mark.parametrize(
    "record",
    [
        {"version": 2, "settings": {}, "recent_projects": None},
        {"version": 2, "settings": {"max_steps": True}},
        {"version": 2, "settings": {"streaming": "false"}},
    ],
)
def test_corrupt_field_types_use_defaults_without_overwriting_file(tmp_path, record):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(record))
    original = path.read_bytes()
    store = ConfigurationStore(tmp_path, MemoryCredentials())
    assert store.load() == ModelSettings() and store.readonly and store.warning
    store.remember_project(tmp_path)
    assert path.read_bytes() == original


def test_windows_vault_real_write_read_enumerate_delete():
    vault = WindowsCredentials()
    prefix = "MiniCodex/verification/" + uuid4().hex + "/"
    target = prefix + "key"
    secret = "synthetic-测试-" + uuid4().hex
    try:
        vault.write(target, secret)
        assert vault.read(target) == secret and target in vault.targets(prefix)
        vault.delete(target)
        assert vault.read(target) is None
    finally:
        vault.delete(target)


def test_restart_restores_public_settings_and_opted_in_key(tmp_path):
    vault = MemoryCredentials()
    settings = ModelSettings(
        mode="compatible",
        base_url="https://example.invalid/v1",
        model="fixture",
        api_key="synthetic-private-key",
        max_steps=31,
        execution_mode="native",
        project_python=r"C:\Python\python.exe",
    )
    facade = ApplicationFacade(data_dir=tmp_path, credentials=vault)
    facade.configure(settings, remember_key=True)
    facade.close()
    assert settings.api_key not in (tmp_path / "settings.json").read_text()
    restored = ApplicationFacade(data_dir=tmp_path, credentials=vault)
    try:
        assert restored.settings == settings and restored.configuration.remember_key
    finally:
        restored.close()


def test_memory_only_key_and_cross_endpoint_reference_rejected(tmp_path):
    vault = MemoryCredentials()
    store = ConfigurationStore(tmp_path, vault)
    settings = ModelSettings(api_key="only-in-memory")
    store.save(settings, remember=False)
    assert not ConfigurationStore(tmp_path, vault).load().api_key and not vault.values
    store.save(settings, remember=True)
    record = json.loads(store.path.read_text())
    record["settings"]["base_url"] = "https://another.invalid/v1"
    store.path.write_text(json.dumps(record))
    other = ConfigurationStore(tmp_path, vault)
    assert not other.load().api_key and other.readonly and other.warning


def test_failed_json_commit_keeps_previous_config_and_key(tmp_path, monkeypatch):
    vault = MemoryCredentials()
    store = ConfigurationStore(tmp_path, vault)
    first = ModelSettings(api_key="old-private-value")
    store.save(first, remember=True)
    original = store.path.read_bytes()

    def fail(*args):
        raise OSError("synthetic file failure")

    monkeypatch.setattr("minicodex.backend.persistence.configuration.os.replace", fail)
    with pytest.raises(OSError):
        store.save(replace(first, api_key="new-private-value"), remember=True)
    assert store.path.read_bytes() == original
    assert list(vault.values.values()) == ["old-private-value"]


def test_vault_failure_never_falls_back_to_plaintext(tmp_path):
    class Broken(MemoryCredentials):
        def write(self, *args):
            raise OSError("vault unavailable")

    store = ConfigurationStore(tmp_path, Broken())
    with pytest.raises(OSError):
        store.save(ModelSettings(api_key="never-write-this"), remember=True)
    assert not store.path.exists()


def test_legacy_plaintext_migrates_without_plaintext_backup(tmp_path):
    vault = MemoryCredentials()
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "version": 1,
                "mode": "compatible",
                "base_url": "https://example.invalid/v1",
                "model": "fixture",
                "api_key": "legacy-secret",
                "max_steps": 33,
            }
        )
    )
    store = ConfigurationStore(tmp_path, vault)
    settings = store.load()
    assert settings.api_key == "legacy-secret" and settings.max_steps == 33
    assert "legacy-secret" not in store.path.read_text() and not vault.values
    assert not ConfigurationStore(tmp_path, vault).load().api_key
    assert not list(tmp_path.glob("*.bak"))


@pytest.mark.parametrize(
    "raw", ['{"version":999}', "{broken", '{"version":2,"settings":{"max_steps":"bad"}}']
)
def test_invalid_configuration_is_preserved_until_explicit_reset(tmp_path, raw):
    path = tmp_path / "settings.json"
    path.write_text(raw)
    store = ConfigurationStore(tmp_path, MemoryCredentials())
    assert store.load() == ModelSettings() and store.readonly
    with pytest.raises(ValueError):
        store.save(ModelSettings(), remember=False)
    assert path.read_text() == raw
    store.reset()
    assert json.loads(path.read_text())["version"] == 2


def test_forget_and_endpoint_switch_remove_old_owned_credentials(tmp_path):
    vault = MemoryCredentials()
    store = ConfigurationStore(tmp_path, vault)
    store.save(ModelSettings(api_key="old-key"), remember=True)
    store.save(ModelSettings(base_url="https://new.invalid/v1", api_key="new-key"), remember=True)
    assert list(vault.values.values()) == ["new-key"]
    store.forget()
    assert not vault.values and json.loads(store.path.read_text())["credential_ref"] is None


@pytest.mark.parametrize("mode", ["native", "docker", "readonly", "write"])
def test_legacy_execution_mode_infers_environment_without_changing_permissions(tmp_path, mode):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"version": 2, "settings": {"execution_mode": mode, "approval_policy": "ask"}})
    )
    store = ConfigurationStore(tmp_path, MemoryCredentials())
    settings = store.load()
    assert not store.readonly and not store.warning
    assert settings.execution_mode == mode
    assert settings.execution_environment == ("docker" if mode == "docker" else "native")
    readonly = replace(settings, execution_mode="readonly")
    store.save(readonly, remember=False)
    restored = ConfigurationStore(tmp_path, MemoryCredentials()).load()
    assert restored == readonly and restored.permission_level == "readonly"
    assert restored.execution_environment == settings.execution_environment


@pytest.mark.parametrize("value", ["unknown", 123])
def test_invalid_saved_execution_environment_fails_closed(tmp_path, value):
    path = tmp_path / "settings.json"
    raw = json.dumps({"version": 2, "settings": {"execution_environment": value}})
    path.write_text(raw)
    store = ConfigurationStore(tmp_path, MemoryCredentials())
    assert store.load() == ModelSettings() and store.readonly
    assert path.read_text() == raw
