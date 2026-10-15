import sys

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QApplication, QScrollArea

from minicodex.backend.application.dependencies import discover
from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.application.settings import ModelSettings
from minicodex.frontend.ui.layout import fit_window
from minicodex.frontend.ui.main_window import MainWindow
from minicodex.frontend.ui.settings_dialog import SettingsDialog


def test_saved_recent_projects_reopen_and_shortcuts_work(qtbot, tmp_path):
    first = tmp_path / "中文 project"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    data = tmp_path / "data"
    window = MainWindow(ApplicationFacade(data_dir=data))
    qtbot.addWidget(window)
    window.show()
    window.open_workspace(first)
    window.open_workspace(second)
    assert window.close()
    restored = MainWindow(ApplicationFacade(data_dir=data))
    qtbot.addWidget(restored)
    restored.show()
    restored.activateWindow()
    qtbot.waitUntil(restored.isActiveWindow)
    qtbot.wait(50)
    try:
        assert restored.recent_projects.count() == 3
        restored.open_recent_project(2)
        assert restored.facade.thread.workspace == first
        previous = restored.facade.thread.thread_id
        qtbot.keyClick(restored.prompt, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
        assert restored.facade.thread.thread_id != previous
        qtbot.keyClick(restored, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
        assert restored.prompt.hasFocus()
    finally:
        restored.close()


def test_dependency_report_missing_tools_and_custom_interpreter(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "minicodex.backend.application.dependencies.shutil.which", lambda *args, **kw: None
    )
    report = discover(ModelSettings(), tmp_path)
    assert report[0].path is None and "未找到" in report[0].hint
    assert report[1].path is None and "安装 Git" in report[1].hint
    settings = ModelSettings(project_python=sys.executable)
    report = discover(settings, tmp_path)
    assert report[0].path and report[0].path.lower().endswith("python.exe")
    dialog = SettingsDialog(settings, workspace=tmp_path)
    qtbot.addWidget(dialog)
    assert "客户端自带的运行时" in dialog.dependency_report.text()
    assert "未找到" in dialog.dependency_report.text()


def test_loaded_key_is_cleared_when_changing_service(qtbot):
    dialog = SettingsDialog(
        ModelSettings(mode="compatible", model="fixture", api_key="loaded-private")
    )
    qtbot.addWidget(dialog)
    dialog.base_url.setText("https://another.invalid/v1")
    assert not dialog.key.text()
    assert "服务地址已变更" in dialog.key.placeholderText()


def test_small_logical_screen_size_and_scrollable_settings(qtbot):
    assert fit_window(QSize(960, 540), QSize(1280, 920)) == QSize(936, 516)
    dialog = SettingsDialog(ModelSettings())
    qtbot.addWidget(dialog)
    dialog.resize(560, 520)
    dialog.show()
    scroll = dialog.findChild(QScrollArea)
    assert scroll is not None
    qtbot.waitUntil(lambda: scroll.verticalScrollBar().maximum() > 0)
    scroll.ensureWidgetVisible(dialog.project_python)
    assert scroll.verticalScrollBar().value() > 0
    assert dialog.project_python is not None and dialog.remember_key is not None


def test_native_startup_fits_1080p_at_200_percent(qtbot, monkeypatch):
    class Screen:
        def availableGeometry(self):
            return QRect(0, 0, 960, 540)

    monkeypatch.setattr(QApplication, "primaryScreen", lambda: Screen())
    monkeypatch.setattr(QApplication, "platformName", lambda: "windows")
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(50)
    try:
        assert window.width() <= 936 and window.height() <= 516
        for control in (window.prompt, window.send_button, window.settings_button):
            bounds = QRect(control.mapTo(window, QPoint(0, 0)), control.size())
            assert window.rect().contains(bounds) and control.isVisible()
        assert not window.subtitle.isVisible()
        sidebar = next(
            area
            for area in window.findChildren(QScrollArea)
            if area.widget().objectName() == "sidebar"
        )
        assert sidebar.verticalScrollBar().maximum() > 0
    finally:
        window.close()
