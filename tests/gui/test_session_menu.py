import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QMessageBox

from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.llm.fake import FakeLLMProvider
from minicodex.frontend.ui.main_window import MainWindow
from minicodex.frontend.ui.session_row import SessionRow


def row_for(window, identity):
    for index in range(window.sessions.count()):
        item = window.sessions.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == identity:
            return window.sessions.itemWidget(item)
    raise AssertionError("Expected row not found")


def test_row_menu_renames_target_without_switching_current_conversation(
    qtbot, tmp_path, monkeypatch
):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_workspace(tmp_path)
        first = window.facade.thread
        window.new_session()
        current = window.facade.thread
        window.prompt.setPlainText("draft belongs to current conversation")
        first_row = row_for(window, first.thread_id)
        assert isinstance(first_row, SessionRow)
        assert [action.text() for action in first_row.menu.actions()] == ["重命名", "删除对话"]
        monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("独立命名", True))
        first_row.rename_action.trigger()
        assert first.title == "独立命名" and window.facade.thread is current
        assert window.prompt.toPlainText() == "draft belongs to current conversation"
        assert row_for(window, first.thread_id).title_button.accessibleName() == "独立命名"
        assert not hasattr(window, "rename_button") and not hasattr(window, "export_button")
        assert not hasattr(window, "export_content")
        row_for(window, first.thread_id).title_button.click()
        assert window.facade.thread is first and window.conversation_title.text() == "独立命名"
    finally:
        window.close()


def test_delete_inactive_current_and_last_conversation_stays_deleted_after_restart(
    qtbot, tmp_path, monkeypatch
):
    project, data = tmp_path / "project", tmp_path / "data"
    project.mkdir()
    keep = project / "keep.txt"
    keep.write_text("user project")
    window = MainWindow(ApplicationFacade(data_dir=data))
    qtbot.addWidget(window)
    window.show()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    try:
        window.open_workspace(project)
        first = window.facade.thread
        window.new_session()
        second = window.facade.thread
        window.new_session()
        third = window.facade.thread
        window.prompt.setPlainText("current draft")
        row_for(window, first.thread_id).delete_action.trigger()
        assert window.facade.thread is third and window.prompt.toPlainText() == "current draft"
        assert window.sessions.count() == 2
        row_for(window, third.thread_id).delete_action.trigger()
        assert window.facade.thread is second and not window.prompt.toPlainText()
        row_for(window, second.thread_id).delete_action.trigger()
        assert window.facade.thread is None and window.sessions.count() == 0
        assert not window.send_button.isEnabled() and window.new_session_button.isEnabled()
        assert not window.chat.cards and keep.read_text() == "user project"
    finally:
        window.close()
    reopened = MainWindow(ApplicationFacade(data_dir=data))
    qtbot.addWidget(reopened)
    try:
        assert reopened.sessions.count() == 0 and reopened.facade.thread is None
        reopened.open_workspace(project)
        assert reopened.facade.thread.thread_id not in {
            first.thread_id,
            second.thread_id,
            third.thread_id,
        }
    finally:
        reopened.close()


def test_cancel_delete_and_delete_save_failure_keep_original_conversation(
    qtbot, tmp_path, monkeypatch
):
    window = MainWindow(ApplicationFacade(data_dir=tmp_path / "data"))
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_workspace(tmp_path)
        thread = window.facade.thread
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
        row_for(window, thread.thread_id).delete_action.trigger()
        assert window.facade.thread is thread and window.sessions.count() == 1
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
        warnings = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))

        def fail(identity):
            raise OSError("synthetic save failure")

        monkeypatch.setattr(window.facade.thread_manager.store, "delete", fail)
        row_for(window, thread.thread_id).delete_action.trigger()
        assert warnings and window.facade.thread is thread and window.sessions.count() == 1
    finally:
        window.close()


def test_running_turn_disables_menu_and_rejects_backend_delete(qtbot, tmp_path):
    facade = ApplicationFacade(FakeLLMProvider(delay_s=10))
    window = MainWindow(facade)
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_workspace(tmp_path)
        thread = facade.thread
        window.prompt.setPlainText("wait")
        window.send_turn()
        row = row_for(window, thread.thread_id)
        assert not row.more_button.isEnabled()
        with pytest.raises(RuntimeError, match="停止"):
            facade.delete_thread(thread.thread_id)
        window.delete_session(thread.thread_id)
        assert facade.thread is thread
        window.stop_turn()
        qtbot.waitUntil(lambda: not facade.is_running, timeout=5000)
        assert row.more_button.isEnabled()
    finally:
        facade.close()
