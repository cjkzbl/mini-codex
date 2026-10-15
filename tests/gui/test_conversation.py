from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog

from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.core.models import ModelResponse, ToolCall
from minicodex.backend.llm.fake import FakeLLMProvider
from minicodex.frontend.ui.main_window import MainWindow


def test_independent_names_and_manual_name_survives_first_prompt(qtbot, tmp_path, monkeypatch):
    facade = ApplicationFacade(FakeLLMProvider(delay_s=0))
    window = MainWindow(facade)
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_workspace(tmp_path)
        first = facade.thread
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("修复计算器", True))
        window.rename_session()
        assert window.conversation_title.text() == "修复计算器"
        window.prompt.setPlainText("a prompt that must not replace the title")
        window.send_turn()
        qtbot.waitUntil(lambda: not facade.is_running, timeout=5000)
        assert first.title == "修复计算器"
        window.new_session()
        second = facade.thread
        assert second.title == "新对话" and not second.history
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("分析结构", True))
        window.rename_session()
        assert second.title == "分析结构" and first.title == "修复计算器"
        for index in range(window.sessions.count()):
            item = window.sessions.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == first.thread_id:
                window.resume_session(item)
                break
        assert window.conversation_title.text() == "修复计算器"
        assert "a prompt that must not replace the title" in window.chat.toPlainText()
    finally:
        facade.close()


def test_step_output_and_multiple_tools_restore_with_turn_metrics(qtbot, tmp_path):
    (tmp_path / "a.py").write_text("answer = 42\n", "utf-8")
    facade = ApplicationFacade(
        FakeLLMProvider(
            [
                ModelResponse(
                    "先查看文件。",
                    (
                        ToolCall("list", "list_files", {}),
                        ToolCall("read", "read_file", {"path": "a.py"}),
                    ),
                ),
                ModelResponse("分析完毕：answer 为 42。"),
            ],
            delay_s=0,
        )
    )
    window = MainWindow(facade)
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_workspace(tmp_path)
        first = facade.thread
        window.prompt.setPlainText("理解这个项目")
        window.send_turn()
        qtbot.waitUntil(lambda: not facade.is_running, timeout=5000)
        turn = first.turns[0]
        card = window.chat.cards[turn.turn_id]
        steps = list(card.steps.values())
        assert len(steps) == 2 and steps[0].output == "先查看文件。"
        assert set(steps[0].tools) == {"list", "read"} and not steps[1].tools
        assert steps[0].tools["read"].state == "成功"
        assert "42" in steps[0].tools["read"].result
        assert '"path": "a.py"' in steps[0].tools["read"].arguments
        assert "用时" in card.summary.text() and "已编辑 0 个文件" in card.summary.text()
        assert "模型 2 次 / 工具 2 次" in card.summary.text()
        assert not hasattr(window, "inspector") and not hasattr(window, "details_button")
        before = window.chat.toPlainText()
        window.new_session()
        window.load_thread(facade.resume(first.thread_id))
        assert window.chat.toPlainText() == before
        restored = window.chat.cards[turn.turn_id]
        assert len(restored.steps) == 2 and restored.summary.text() == card.summary.text()
        qtbot.waitUntil(
            lambda: window.chat.verticalScrollBar().value()
            == window.chat.verticalScrollBar().maximum()
        )
    finally:
        facade.close()


def test_scrolling_history_does_not_jump_to_live_output(qtbot, tmp_path):
    facade = ApplicationFacade(FakeLLMProvider([ModelResponse("很长的回答\n" * 90)], delay_s=0.003))
    window = MainWindow(facade)
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_workspace(tmp_path)
        window.prompt.setPlainText("长回答")
        window.send_turn()
        bar = window.chat.verticalScrollBar()
        qtbot.waitUntil(lambda: bar.maximum() > 200, timeout=5000)
        bar.setValue(0)
        window.chat.follow_position(0)
        qtbot.waitUntil(lambda: not facade.is_running, timeout=5000)
        assert bar.value() == 0
        window.chat.scroll_to_bottom()
        qtbot.waitUntil(lambda: bar.value() == bar.maximum())
    finally:
        facade.close()
