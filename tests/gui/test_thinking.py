import asyncio
from threading import Event

from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.core.models import ModelResponse, ToolCall
from minicodex.backend.llm.fake import FakeLLMProvider
from minicodex.frontend.ui.main_window import MainWindow


def latest_step(window):
    card = window.chat.cards[window.facade.thread.turns[-1].turn_id]
    return list(card.steps.values())[-1]


def test_thinking_animates_wait_only_and_restored_history_remains_idle(qtbot, tmp_path):
    class GatedProvider(FakeLLMProvider):
        start_output = Event()
        finish = Event()

        async def stream(self, messages, tools):
            while not self.start_output.is_set():
                await asyncio.sleep(0.01)
            yield "已生成回答"
            while not self.finish.is_set():
                await asyncio.sleep(0.01)
            yield ModelResponse("已生成回答")

    provider = GatedProvider()
    window = MainWindow(ApplicationFacade(provider))
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_workspace(tmp_path)
        thread = window.facade.thread
        window.prompt.setPlainText("wait for model")
        window.send_turn()
        qtbot.waitUntil(lambda: bool(window.chat.cards[thread.turns[-1].turn_id].steps))
        step = latest_step(window)
        qtbot.waitUntil(step.heading.timer.isActive)
        first_frame = step.heading.grab().toImage()
        phase = step.heading.phase
        qtbot.waitUntil(lambda: step.heading.phase != phase)
        assert first_frame != step.heading.grab().toImage()
        step.heading.hide()
        assert not step.heading.timer.isActive()
        step.heading.show()
        assert step.heading.timer.isActive()
        provider.start_output.set()
        qtbot.waitUntil(lambda: step.output == "已生成回答")
        assert not step.heading.active and not step.heading.timer.isActive()
        provider.finish.set()
        qtbot.waitUntil(lambda: not window.facade.is_running)
        before = window.chat.toPlainText()
        window.new_session()
        window.load_thread(window.facade.resume(thread.thread_id))
        assert window.chat.toPlainText() == before
        for restored in window.chat.cards[thread.turns[0].turn_id].steps.values():
            assert not restored.heading.active and not restored.heading.timer.isActive()
    finally:
        window.facade.close()


def test_next_model_step_restarts_indicator_and_stop_clears_it_immediately(qtbot, tmp_path):
    class ThenWait(FakeLLMProvider):
        calls = 0

        async def stream(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                yield ModelResponse(tool_calls=(ToolCall("list", "list_files", {}),))
            else:
                await asyncio.sleep(30)
                yield ModelResponse("done")

    window = MainWindow(ApplicationFacade(ThenWait()))
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_workspace(tmp_path)
        window.prompt.setPlainText("list and analyze")
        window.send_turn()
        card = window.chat.cards[window.facade.active_turn.turn_id]
        qtbot.waitUntil(lambda: len(card.steps) == 2)
        first, second = list(card.steps.values())
        assert first.tools["list"].state == "成功" and not first.heading.active
        qtbot.waitUntil(second.heading.timer.isActive)
        window.stop_turn()
        assert not second.heading.active and not second.heading.timer.isActive()
        qtbot.waitUntil(lambda: not window.facade.is_running)
        assert "已停止" in card.summary.text()
    finally:
        window.facade.close()


def test_model_failure_stops_thinking_indicator(qtbot, tmp_path):
    class Broken(FakeLLMProvider):
        fail = Event()

        async def stream(self, messages, tools):
            while not self.fail.is_set():
                await asyncio.sleep(0.01)
            raise RuntimeError("synthetic failure")
            yield  # pragma: no cover -- async generator interface

    provider = Broken()
    window = MainWindow(ApplicationFacade(provider))
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_workspace(tmp_path)
        window.prompt.setPlainText("fail")
        window.send_turn()
        qtbot.waitUntil(lambda: bool(window.chat.cards[window.facade.active_turn.turn_id].steps))
        step = latest_step(window)
        qtbot.waitUntil(step.heading.timer.isActive)
        provider.fail.set()
        qtbot.waitUntil(lambda: not window.facade.is_running)
        assert window.status_label.text() == "失败"
        assert not step.heading.active and not step.heading.timer.isActive()
    finally:
        window.facade.close()
