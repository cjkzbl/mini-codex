from collections.abc import AsyncIterator

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMessageBox

from minicodex.backend.application.commands import StartTurn
from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.core.events import EventKind, RuntimeEvent
from minicodex.backend.core.models import Message, ModelResponse
from minicodex.backend.llm.fake import FakeLLMProvider
from minicodex.frontend.ui.main_window import MainWindow


@pytest.fixture
def window(qtbot):
    facade = ApplicationFacade(FakeLLMProvider(delay_s=0.002))
    widget = MainWindow(facade)
    qtbot.addWidget(widget)
    widget.show()
    yield widget
    facade.close()


def send(window, qtbot, text="hello"):
    window.prompt.setPlainText(text)
    qtbot.mouseClick(window.send_button, Qt.MouseButton.LeftButton)


def test_directory_picker_and_two_turns(window, qtbot, tmp_path, monkeypatch):
    project = tmp_path / "中文 project"
    project.mkdir()
    monkeypatch.setattr(
        "minicodex.frontend.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *args: str(project),
    )
    qtbot.mouseClick(window.open_button, Qt.MouseButton.LeftButton)
    assert window.facade.thread.workspace == project.resolve()
    for index in (1, 2):
        send(window, qtbot)
        qtbot.waitUntil(lambda: window.status_label.text() == "已完成", timeout=5000)
        assert f"第 {index} 轮" in window.chat.toPlainText()
    assert len(window.facade.thread.history) == 8
    assert "list_files" in window.chat.toPlainText()
    assert window.send_button.isEnabled()


def test_window_remains_responsive_and_stop_interrupts_sleep(qtbot, tmp_path):
    facade = ApplicationFacade(FakeLLMProvider(delay_s=10))
    widget = MainWindow(facade)
    qtbot.addWidget(widget)
    widget.show()
    try:
        widget.open_workspace(tmp_path)
        send(widget, qtbot)
        ticks = []
        timer = QTimer(widget)
        timer.setInterval(5)
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start()
        qtbot.waitUntil(lambda: len(ticks) >= 5)
        assert facade.is_running
        qtbot.mouseClick(widget.stop_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: widget.status_label.text() == "已停止", timeout=2000)
        assert widget.facade.thread.turns[-1].events[-1].kind == EventKind.TURN_CANCELLED
        assert "已停止" in widget.chat.toPlainText()
        assert widget.open_button.isEnabled()
    finally:
        assert facade.close()


def test_reject_overlapping_turn_and_workspace_change(window, qtbot, tmp_path):
    window.open_workspace(tmp_path)
    send(window, qtbot)
    thread_id = window.facade.thread.thread_id
    with pytest.raises(RuntimeError):
        window.facade.start(StartTurn(thread_id, "another task"))
    with pytest.raises(RuntimeError):
        window.facade.select_workspace(tmp_path)
    qtbot.waitUntil(lambda: not window.facade.is_running, timeout=5000)


def test_foreign_and_duplicate_events_do_not_change_chat(window, qtbot, tmp_path):
    window.open_workspace(tmp_path)
    send(window, qtbot)
    before = window.chat.toPlainText()
    window.on_event(RuntimeEvent(EventKind.MODEL_DELTA, "foreign", "foreign", "step", 1, "BAD"))
    assert window.chat.toPlainText() == before
    qtbot.waitUntil(lambda: not window.facade.is_running, timeout=5000)
    after = window.chat.toPlainText()
    window.on_event(
        RuntimeEvent(
            EventKind.MODEL_DELTA, window.facade.thread.thread_id, window._turn_id, "step", 1, "BAD"
        )
    )
    assert window.chat.toPlainText() == after


def test_close_running_turn_prompts_and_cleans_worker(window, qtbot, tmp_path, monkeypatch):
    window.open_workspace(tmp_path)
    send(window, qtbot)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    assert not window.close()
    assert window.facade.worker.alive
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    assert window.close()
    assert not window.facade.worker.alive


def test_backpressure_preserves_terminal_event(qtbot, tmp_path):
    class Fast(FakeLLMProvider):
        async def stream(
            self, messages: list[Message], tools
        ) -> AsyncIterator[str | ModelResponse]:
            for _ in range(700):
                yield "a"
            yield ModelResponse(text="a" * 700)

    facade = ApplicationFacade(Fast())
    try:
        thread = facade.select_workspace(tmp_path)
        facade.start(StartTurn(thread.thread_id, "hello"))
        collected = []

        def drain():
            collected.extend(facade.drain_events())
            return not facade.is_running

        qtbot.waitUntil(drain, timeout=5000)
        assert len(collected) == 705
        assert collected[-1].kind == EventKind.TURN_COMPLETED
        assert [e.sequence for e in collected] == list(range(1, 706))
    finally:
        assert facade.close()


def test_cancel_with_full_event_queue_and_close_without_consumer(qtbot, tmp_path):
    class Fast(FakeLLMProvider):
        async def stream(
            self, messages: list[Message], tools
        ) -> AsyncIterator[str | ModelResponse]:
            for _ in range(2000):
                yield "a"

    facade = ApplicationFacade(Fast())
    try:
        thread = facade.select_workspace(tmp_path)
        facade.start(StartTurn(thread.thread_id, "hello"))
        qtbot.waitUntil(lambda: facade._events.full(), timeout=3000)
        facade.cancel()
    finally:
        assert facade.close()
    assert not facade.worker.alive


def test_immediate_cancel_is_a_terminal_cancelled_turn(qtbot, tmp_path):
    facade = ApplicationFacade(FakeLLMProvider(delay_s=10))
    try:
        thread = facade.select_workspace(tmp_path)
        facade.start(StartTurn(thread.thread_id, "hello"))
        facade.cancel()
        events = []

        def drain():
            events.extend(facade.drain_events())
            return not facade.is_running

        qtbot.waitUntil(drain, timeout=2000)
        assert events[-1].kind == EventKind.TURN_CANCELLED
        assert sum(e.kind == EventKind.TURN_CANCELLED for e in events) == 1
    finally:
        assert facade.close()


def test_gui_can_restore_prior_in_memory_session(window, qtbot, tmp_path):
    window.open_workspace(tmp_path)
    first_id = window.facade.thread.thread_id
    send(window, qtbot, "first session task")
    qtbot.waitUntil(lambda: not window.facade.is_running, timeout=5000)
    window.new_session()
    assert window.facade.thread.thread_id != first_id
    assert "first session task" not in window.chat.toPlainText()
    for index in range(window.sessions.count()):
        item = window.sessions.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == first_id:
            window.resume_session(item)
            break
    assert window.facade.thread.thread_id == first_id
    assert "first session task" in window.chat.toPlainText()
