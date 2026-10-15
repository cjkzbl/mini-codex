from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.application.settings import ModelSettings
from minicodex.frontend.ui.main_window import MainWindow
from minicodex.frontend.ui.settings_dialog import SettingsDialog


@pytest.mark.parametrize("level", ["readonly", "default", "full"])
@pytest.mark.parametrize("environment", ["native", "docker"])
def test_environment_selector_preserves_permissions(qtbot, level, environment):
    original_environment = "docker" if environment == "native" else "native"
    original_mode = "readonly" if level == "readonly" else original_environment
    dialog = SettingsDialog(
        ModelSettings(
            mode="compatible",
            model="fixture-model",
            api_key="memory-key",
            execution_mode=original_mode,
            execution_environment=original_environment,
            approval_policy="never" if level == "full" else "ask",
            docker_image="python:3.12-slim",
        )
    )
    qtbot.addWidget(dialog)
    assert dialog.execution_environment.currentData() == original_environment
    assert [dialog.execution_environment.itemData(i) for i in range(2)] == ["native", "docker"]
    dialog.execution_environment.setCurrentIndex(dialog.execution_environment.findData(environment))
    assert dialog.docker_image.isEnabled() is (environment == "docker")
    dialog.save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    saved = dialog.result_settings
    assert saved.permission_level == level
    assert saved.execution_environment == environment
    assert saved.execution_mode == ("readonly" if level == "readonly" else environment)
    assert saved.docker_image == "python:3.12-slim"
    assert saved.mode == "compatible" and saved.model == "fixture-model"
    assert saved.api_key == "memory-key"


def test_opening_and_saving_legacy_write_settings_does_not_enable_commands(qtbot):
    original = ModelSettings(execution_mode="write")
    dialog = SettingsDialog(original)
    qtbot.addWidget(dialog)
    dialog.save()
    assert dialog.result_settings == original


@pytest.fixture
def client(qtbot, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    facade = ApplicationFacade(data_dir=tmp_path / "data")
    window = MainWindow(facade)
    qtbot.addWidget(window)
    window.open_workspace(project)
    try:
        yield window
    finally:
        window.close()


def choose_docker(dialog):
    dialog.execution_environment.setCurrentIndex(dialog.execution_environment.findData("docker"))
    dialog.docker_image.setText("python:3.12-slim")
    dialog.save()
    return dialog.result()


def test_docker_selection_survives_readonly_restart_and_permission_changes(
    client, qtbot, monkeypatch
):
    window = client
    facade = window.facade
    data_dir = Path(facade.configuration.directory)
    thread = facade.thread
    window.prompt.setPlainText("keep my draft")
    monkeypatch.setattr(SettingsDialog, "exec", choose_docker)
    window.configure_model()
    assert facade.thread is thread and window.prompt.toPlainText() == "keep my draft"
    assert facade.settings.permission_level == "readonly"
    assert facade.settings.execution_environment == "docker"
    assert facade.runtime.settings.execution_mode == "readonly"
    assert "Docker 沙箱" in window.permission_picker.toolTip()
    assert window.close()

    restored = MainWindow(ApplicationFacade(data_dir=data_dir))
    qtbot.addWidget(restored)
    try:
        settings = SettingsDialog(restored.facade.settings)
        qtbot.addWidget(settings)
        assert settings.execution_environment.currentData() == "docker"
        assert settings.docker_image.text() == "python:3.12-slim"
        for level in ("default", "full", "readonly", "default"):
            restored.change_permissions(restored.permission_picker.findData(level))
            assert restored.facade.settings.execution_environment == "docker"
            assert restored.facade.settings.execution_mode == (
                "readonly" if level == "readonly" else "docker"
            )
            assert restored.facade.configuration.load() == restored.facade.settings
    finally:
        restored.close()


def test_cancelling_environment_settings_preserves_current_and_saved_state(client, monkeypatch):
    original = client.facade.settings

    def cancel(dialog):
        dialog.execution_environment.setCurrentIndex(
            dialog.execution_environment.findData("docker")
        )
        dialog.reject()
        return dialog.result()

    monkeypatch.setattr(SettingsDialog, "exec", cancel)
    client.configure_model()
    assert client.facade.settings == original
    assert client.facade.configuration.load() == original
    assert "Windows 原生执行" in client.permission_picker.toolTip()


def test_environment_save_failure_does_not_change_runner_or_permissions(client, monkeypatch, qtbot):
    original = client.facade.settings

    def fail(*args, **kwargs):
        raise OSError("synthetic configuration failure")

    monkeypatch.setattr(SettingsDialog, "exec", choose_docker)
    monkeypatch.setattr(client.facade.configuration, "save", fail)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    client.configure_model()
    assert client.facade.settings == original
    assert client.facade.runtime.settings == original
    assert client.facade.configuration.load() == original
    reopened = SettingsDialog(client.facade.settings)
    qtbot.addWidget(reopened)
    assert reopened.execution_environment.currentData() == "native"
