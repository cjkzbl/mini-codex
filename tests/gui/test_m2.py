import json
import sys

import pytest
from PySide6.QtCore import Qt

from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.history import validate_history
from minicodex.backend.core.models import ModelResponse, ToolCall
from minicodex.backend.llm.fake import FakeLLMProvider
from minicodex.frontend.ui.main_window import MainWindow


def command_logs(window):
    return "\n".join(
        tool.log
        for card in window.chat.cards.values()
        for step in card.steps.values()
        for tool in step.tools.values()
    )


@pytest.mark.parametrize("decision", ["approve", "reject", "stop"])
def test_gui_approval_diff_and_stop_do_not_execute_without_permission(qtbot, tmp_path, decision):
    (tmp_path / "a").write_bytes(b"old\n")
    provider = FakeLLMProvider(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        "patch",
                        "apply_patch",
                        {"patch": "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n"},
                    ),
                )
            ),
            ModelResponse(text="done"),
        ],
        delay_s=0,
    )
    facade = ApplicationFacade()
    facade.configure(ModelSettings(execution_mode="write"))
    facade.runtime.provider = provider
    window = MainWindow(facade)
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_workspace(tmp_path)
        window.prompt.setPlainText("change a")
        window.send_turn()
        qtbot.waitUntil(lambda: window._approval_id is not None, timeout=5000)
        assert "+new" in window.chat.toPlainText()
        assert (tmp_path / "a").read_bytes() == b"old\n"
        if decision == "stop":
            window.stop_turn()
        else:
            qtbot.mouseClick(
                window.approve_button if decision == "approve" else window.reject_button,
                Qt.MouseButton.LeftButton,
            )
        qtbot.waitUntil(lambda: not facade.is_running, timeout=5000)
        assert (tmp_path / "a").read_bytes() == (b"new\n" if decision == "approve" else b"old\n")
        assert not window.approval_panel.isVisible()
        turn = facade.thread.turns[0]
        assert turn.edited_files == ({"a"} if decision == "approve" else set())
        assert turn.duration_s > 0
        assert f"已编辑 {int(decision == 'approve')} 个文件" in window.chat.toPlainText()
        validate_history(facade.thread.history)
    finally:
        facade.close()


def test_gui_stop_active_native_command_waits_for_cleanup(qtbot, tmp_path):
    provider = FakeLLMProvider(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        "command",
                        "run_command",
                        {
                            "argv": [
                                sys.executable,
                                "-c",
                                "import time;print('started',flush=True);time.sleep(30)",
                            ]
                        },
                    ),
                )
            )
        ],
        delay_s=0,
    )
    facade = ApplicationFacade()
    facade.configure(ModelSettings(execution_mode="native"))
    facade.runtime.provider = provider
    window = MainWindow(facade)
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_workspace(tmp_path)
        window.prompt.setPlainText("run command")
        window.send_turn()
        qtbot.waitUntil(lambda: window._approval_id is not None, timeout=5000)
        window.answer_approval(True)
        qtbot.waitUntil(lambda: "started" in command_logs(window), timeout=5000)
        window.stop_turn()
        qtbot.waitUntil(lambda: not facade.is_running, timeout=5000)
        assert window.status_label.text() == "已停止"
        receipt = next(m for m in facade.thread.history if m.role == "tool")
        assert json.loads(json.loads(receipt.content)["result"])["cleanup_verified"]
        validate_history(facade.thread.history)
    finally:
        facade.close()


def test_gui_native_command_streams_logs_and_failure_receipt(qtbot, tmp_path):
    provider = FakeLLMProvider(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        "command",
                        "run_command",
                        {
                            "argv": [
                                sys.executable,
                                "-c",
                                "import sys,time;print('live log',flush=True);"
                                "time.sleep(.3);sys.exit(3)",
                            ]
                        },
                    ),
                )
            ),
            ModelResponse(text="test failed"),
        ],
        delay_s=0,
    )
    facade = ApplicationFacade()
    facade.configure(ModelSettings(execution_mode="native"))
    facade.runtime.provider = provider
    window = MainWindow(facade)
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_workspace(tmp_path)
        window.prompt.setPlainText("run a test")
        window.send_turn()
        qtbot.waitUntil(lambda: window._approval_id is not None, timeout=5000)
        assert "无 OS 沙箱" in window.approval_preview.toPlainText()
        window.answer_approval(True)
        qtbot.waitUntil(lambda: "live log" in command_logs(window), timeout=5000)
        assert facade.is_running
        qtbot.waitUntil(lambda: not facade.is_running, timeout=5000)
        observation = json.loads(provider.requests[1][-1].content)
        assert not observation["success"] and json.loads(observation["result"])["exit_code"] == 3
        card = window.chat.cards[facade.thread.turns[0].turn_id]
        tool = next(iter(card.steps.values())).tools["command"]
        assert tool.state == "失败" and "live log" in tool.log
    finally:
        facade.close()
