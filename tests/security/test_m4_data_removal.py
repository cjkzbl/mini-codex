import json

import pytest

from minicodex.backend.persistence.configuration import ConfigurationStore
from minicodex.frontend.windows_client import recycle_data, validate_data_removal


def test_confirmed_owned_data_can_be_recycled_without_touching_siblings(tmp_path):
    data = tmp_path / "MiniCodex"
    ConfigurationStore(data)
    (data / "sessions.sqlite3").write_bytes(b"synthetic fixture")
    sibling = tmp_path / "user-project.txt"
    sibling.write_text("keep")
    recycle_data(data, data)
    assert not data.exists() and sibling.read_text() == "keep"


def test_arbitrary_or_unmarked_directory_is_never_removed(tmp_path):
    outside = tmp_path / "project"
    outside.mkdir()
    with pytest.raises(ValueError):
        recycle_data(outside, tmp_path / "MiniCodex")
    unmarked = tmp_path / "MiniCodex"
    unmarked.mkdir()
    with pytest.raises((ValueError, OSError)):
        recycle_data(unmarked, unmarked)
    assert outside.exists() and unmarked.exists()


def test_cleanup_refuses_junction_and_keeps_external_data(tmp_path):
    import subprocess

    data = tmp_path / "MiniCodex"
    ConfigurationStore(data)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    # cmd mklink is needed only to create this Windows junction fixture, never for deletion.
    link = data / "link"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True
    )
    assert result.returncode == 0
    try:
        with pytest.raises(ValueError, match="reparse"):
            validate_data_removal(data, data)
        assert (outside / "keep.txt").read_text() == "keep"
    finally:
        link.rmdir()


def test_config_reset_keeps_conversations(tmp_path):
    config = ConfigurationStore(tmp_path)
    (tmp_path / "sessions.sqlite3").write_bytes(b"existing-user-session")
    config.reset()
    assert (tmp_path / "sessions.sqlite3").read_bytes() == b"existing-user-session"
    assert "api_key" not in json.loads(config.path.read_text())["settings"]


def test_running_app_blocks_data_purge_before_resolving_user_data(monkeypatch):
    from minicodex.frontend.windows_client import APP_MUTEX, NamedMutex, purge_default_data

    def unexpected():
        raise AssertionError("Must not access user data while an application is running")

    monkeypatch.setattr("minicodex.backend.persistence.paths.default_data_directory", unexpected)
    presence = NamedMutex(APP_MUTEX)
    try:
        with pytest.raises(RuntimeError, match="关闭"):
            purge_default_data()
    finally:
        presence.close()
