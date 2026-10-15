from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QSplitter

from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.core.models import Message, ModelResponse
from minicodex.backend.llm.fake import FakeLLMProvider
from minicodex.frontend.ui.main_window import MainWindow


def test_enter_sends_shift_enter_newline_and_ime_return_does_not_send(qtbot, tmp_path):
    facade = ApplicationFacade(FakeLLMProvider(delay_s=0))
    window = MainWindow(facade)
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    window.open_workspace(tmp_path)
    try:
        window.prompt.setPlainText("first line")
        window.prompt.moveCursor(window.prompt.textCursor().MoveOperation.End)
        qtbot.keyClick(window.prompt, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        assert window.prompt.toPlainText() == "first line\n" and not facade.is_running
        QApplication.sendEvent(window.prompt, QInputMethodEvent("测试", []))
        qtbot.keyClick(window.prompt, Qt.Key.Key_Return)
        assert not facade.is_running and not facade.thread.turns
        QApplication.sendEvent(window.prompt, QInputMethodEvent("", []))
        repeat = QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "", True
        )
        QApplication.sendEvent(window.prompt, repeat)
        assert not facade.thread.turns
        qtbot.keyClick(window.prompt, Qt.Key.Key_Return)
        qtbot.waitUntil(lambda: window.status_label.text() == "已完成", timeout=5000)
        assert len(facade.thread.turns) == 1 and not window.prompt.toPlainText()
        qtbot.keyClick(window.prompt, Qt.Key.Key_Return)
        assert len(facade.thread.turns) == 1
    finally:
        facade.close()


def test_permission_switch_keeps_named_conversation_history_and_draft(qtbot, tmp_path):
    provider = FakeLLMProvider(
        [ModelResponse("first reply"), ModelResponse("continued")], delay_s=0
    )
    facade = ApplicationFacade(provider, data_dir=tmp_path / "data")
    window = MainWindow(facade)
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_workspace(tmp_path)
        thread = facade.thread
        facade.rename_thread(thread.thread_id, "original name")
        window.prompt.setPlainText("first task")
        window.send_turn()
        qtbot.waitUntil(lambda: not facade.is_running, timeout=5000)
        original_messages = list(thread.history)
        original_view = window.chat.toPlainText()
        window.prompt.setPlainText("unfinished draft")
        for level in ("full", "default", "readonly", "default"):
            window.change_permissions(window.permission_picker.findData(level))
            assert facade.thread is thread and thread.title == "original name"
            assert thread.history == original_messages and window.sessions.count() == 1
            assert window.chat.toPlainText() == original_view
            assert window.prompt.toPlainText() == "unfinished draft"
            assert facade.settings.permission_level == level
            assert facade.runtime.provider is provider
        window.send_turn()
        qtbot.waitUntil(lambda: not facade.is_running, timeout=5000)
        assert facade.thread is thread and len(thread.turns) == 2
        assert Message("user", "first task") in provider.requests[-1]
        assert "continued" in window.chat.toPlainText()
    finally:
        facade.close()


def test_permission_controls_busy_and_save_failure_roll_back(qtbot, tmp_path, monkeypatch):
    facade = ApplicationFacade(FakeLLMProvider(delay_s=10), data_dir=tmp_path / "data")
    window = MainWindow(facade)
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_workspace(tmp_path)
        original = facade.settings

        def fail(*args, **kwargs):
            raise OSError("synthetic save failure")

        monkeypatch.setattr(facade.configuration, "save", fail)
        monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
        window.change_permissions(2)
        assert facade.settings == original and window.permission_picker.currentData() == "readonly"
        window.prompt.setPlainText("waiting")
        window.send_turn()
        assert not window.permission_picker.isEnabled() and not window.model_button.isEnabled()
        window.change_permissions(2)
        assert facade.settings == original
        window.stop_turn()
        qtbot.waitUntil(lambda: not facade.is_running, timeout=5000)
        assert window.permission_picker.isEnabled() and window.model_button.isEnabled()
    finally:
        facade.close()


def test_composer_controls_relocated_and_inspector_removed(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(50)
    try:
        assert not hasattr(window, "inspector") and not hasattr(window, "details_button")
        assert window.findChild(QSplitter).count() == 2
        assert window.permission_picker.count() == 3
        picker = window.permission_picker.mapTo(window, QPoint())
        model = window.model_button.mapTo(window, QPoint())
        prompt_bottom = window.prompt.mapTo(window, QPoint(0, window.prompt.height()))
        assert picker.y() >= prompt_bottom.y() and model.y() >= prompt_bottom.y()
        assert picker.x() < model.x()
        assert "离线演示" in window.model_button.text()
    finally:
        window.close()
