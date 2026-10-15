import pytest
from PySide6.QtWidgets import QMessageBox

from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.models import ModelResponse, ToolCall, TurnStatus
from minicodex.backend.llm.fake import FakeLLMProvider
from minicodex.frontend.ui.main_window import MainWindow


def test_new_window_restores_session_cards_and_continues(qtbot, tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("Keep existing comments.", "utf-8")
    data = tmp_path / "data"
    first = MainWindow(ApplicationFacade(FakeLLMProvider(delay_s=0), data_dir=data))
    qtbot.addWidget(first)
    first.show()
    first.open_workspace(project)
    identity = first.facade.thread.thread_id
    first.facade.rename_thread(identity, "重启可恢复")
    first.prompt.setPlainText("understand project")
    first.send_turn()
    qtbot.waitUntil(lambda: not first.facade.is_running, timeout=7000)
    before = first.chat.toPlainText()
    assert "第 1 轮" in before
    assert first.close()
    second = MainWindow(ApplicationFacade(FakeLLMProvider(delay_s=0), data_dir=data))
    qtbot.addWidget(second)
    second.show()
    try:
        assert second.facade.thread.thread_id == identity
        assert second.conversation_title.text() == "重启可恢复"
        assert second.chat.toPlainText() == before
        assert "自动保存在本机" in second.storage_label.text()
        target = tmp_path / "trace.jsonl"
        monkeypatch.setattr(
            "minicodex.frontend.ui.main_window.QFileDialog.getSaveFileName",
            lambda *args: (str(target), ""),
        )
        second.export_trace()
        assert target.exists() and "understand project" not in target.read_text("utf-8")
        second.prompt.setPlainText("continue")
        second.send_turn()
        qtbot.waitUntil(lambda: not second.facade.is_running, timeout=7000)
        assert len(second.facade.thread.turns) == 2
        assert second.facade.thread.turns[-1].status == TurnStatus.COMPLETED
    finally:
        second.close()


@pytest.mark.parametrize("phase", ["model", "approval"])
def test_close_running_window_persists_cancelled_state(qtbot, tmp_path, monkeypatch, phase):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    project = tmp_path / "project"
    project.mkdir()
    (project / "a").write_bytes(b"old\n")
    data = tmp_path / "data"
    facade = ApplicationFacade(data_dir=data)
    facade.configure(ModelSettings(execution_mode="write"))
    facade.runtime.provider = (
        FakeLLMProvider(delay_s=10)
        if phase == "model"
        else FakeLLMProvider(
            [
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "patch",
                            "apply_patch",
                            {
                                "patch": "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n",
                            },
                        ),
                    )
                ),
            ],
            delay_s=0,
        )
    )
    first = MainWindow(facade)
    qtbot.addWidget(first)
    first.show()
    first.open_workspace(project)
    first.prompt.setPlainText("change a")
    first.send_turn()
    if phase == "approval":
        qtbot.waitUntil(lambda: first._approval_id is not None, timeout=5000)
    else:
        qtbot.waitUntil(
            lambda: bool(facade.thread.turns and facade.thread.turns[0].events), timeout=5000
        )
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    assert first.close()
    second = MainWindow(ApplicationFacade(data_dir=data))
    qtbot.addWidget(second)
    second.show()
    try:
        assert second.facade.thread.turns[0].status == TurnStatus.CANCELLED
        assert "已停止" in second.chat.toPlainText()
        assert not second.approval_panel.isVisible() and not second.facade.is_running
        assert (project / "a").read_bytes() == b"old\n"
    finally:
        second.close()
