"""Per-turn presentation, separate from the messages sent to the model."""

import json
import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from minicodex.backend.core.events import TERMINAL_EVENTS, EventKind, RuntimeEvent
from minicodex.backend.core.models import ThreadState, TurnState, TurnStatus
from minicodex.frontend.ui.thinking import ThinkingLabel


def text_label(text: str, name: str = "muted") -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class Avatar(QWidget):
    def __init__(self, user: bool) -> None:
        super().__init__()
        self.user = user
        self.setFixedSize(32, 32)
        self.setToolTip("你" if user else "MiniCodex")
        self.setAccessibleName("用户头像" if user else "AI 头像")

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#3c4658" if self.user else "#24443c"))
        painter.drawRoundedRect(0, 0, 32, 32, 10, 10)
        painter.setBrush(QColor("#e5e8ed" if self.user else "#a2e9c2"))
        if self.user:
            painter.drawEllipse(12, 7, 8, 8)
            painter.drawRoundedRect(8, 18, 16, 8, 4, 4)
        else:
            painter.setPen(QPen(QColor("#a2e9c2"), 2.6, Qt.PenStyle.SolidLine))
            for angle in range(0, 360, 60):
                radians = math.radians(angle)
                painter.drawLine(
                    16, 16, int(16 + math.cos(radians) * 10), int(16 + math.sin(radians) * 10)
                )
        painter.end()


class ToolCard(QFrame):
    def __init__(self, name: str, arguments: str) -> None:
        super().__init__()
        self.setObjectName("toolCard")
        self.name = name
        self.state = "执行中"
        self.result = ""
        self.log = ""
        self.diff = ""
        self.arguments = arguments
        self.approval = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 8)
        layout.setSpacing(5)
        self.toggle = QToolButton()
        self.toggle.setObjectName("toolToggle")
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setCheckable(True)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.toggled.connect(self.expand)
        layout.addWidget(self.toggle)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setFixedHeight(170)
        self.details.setObjectName("toolDetails")
        self.details.hide()
        layout.addWidget(self.details)
        self.refresh()

    def expand(self, checked: bool) -> None:
        self.details.setVisible(checked)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def refresh(self) -> None:
        self.toggle.setText(f"{self.name}  ·  {self.state}")
        self.toggle.setToolTip("展开 / 收起参数与结果")
        outcome = {"成功": "success", "失败": "failure"}.get(self.state, "pending")
        if self.toggle.property("outcome") != outcome:
            self.toggle.setProperty("outcome", outcome)
            self.toggle.style().unpolish(self.toggle)
            self.toggle.style().polish(self.toggle)
        sections = ["参数\n" + self.arguments] if self.arguments else []
        if self.approval:
            sections.append("审批\n" + self.approval)
        if self.diff:
            sections.append("修改预览\n" + self.diff)
        if self.log:
            sections.append("命令输出\n" + self.log)
        if self.result:
            sections.append("工具结果\n" + self.result)
        bar = self.details.verticalScrollBar()
        position = bar.value()
        following = bar.maximum() - position < 20
        self.details.setPlainText("\n\n".join(sections))
        bar.setValue(bar.maximum() if following else position)


class StepCard(QFrame):
    def __init__(self, number: int) -> None:
        super().__init__()
        self.setObjectName("stepCard")
        self.number = number
        self.output = ""
        self.tools: dict[str, ToolCard] = {}
        self.layout_box = QVBoxLayout(self)
        self.layout_box.setContentsMargins(14, 12, 14, 12)
        self.layout_box.setSpacing(10)
        self.heading = ThinkingLabel(f"步骤 {number}  ·  思考中")
        self.layout_box.addWidget(self.heading)
        self.body = text_label("", "assistantText")
        self.body.hide()
        self.layout_box.addWidget(self.body)

    def status(self, text: str) -> None:
        self.heading.setText(f"步骤 {self.number}  ·  {text}")
        self.heading.set_active(text == "思考中")


class TurnCard(QWidget):
    def __init__(self, turn: TurnState) -> None:
        super().__init__()
        self.turn = turn
        self.steps: dict[str, StepCard] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 18)
        layout.setSpacing(20)
        user = QHBoxLayout()
        user.setAlignment(Qt.AlignmentFlag.AlignTop)
        user.addWidget(Avatar(True), 0, Qt.AlignmentFlag.AlignTop)
        bubble = QFrame()
        bubble.setObjectName("userBubble")
        user_layout = QVBoxLayout(bubble)
        user_layout.setContentsMargins(14, 10, 14, 12)
        user_layout.addWidget(text_label("你", "speaker"))
        user_layout.addWidget(text_label(turn.prompt, "userText"))
        user.addWidget(bubble, 1)
        layout.addLayout(user)
        agent = QHBoxLayout()
        agent.setAlignment(Qt.AlignmentFlag.AlignTop)
        agent.addWidget(Avatar(False), 0, Qt.AlignmentFlag.AlignTop)
        self.agent_body = QVBoxLayout()
        self.agent_body.setSpacing(10)
        self.agent_body.addWidget(text_label("MiniCodex", "speaker"))
        self.summary = text_label("正在准备任务…", "turnSummary")
        self.agent_body.addWidget(self.summary)
        agent.addLayout(self.agent_body, 1)
        layout.addLayout(agent)

    def consume(self, event: RuntimeEvent) -> None:
        if event.kind == EventKind.TURN_STARTED:
            self.summary.setText("正在处理任务…")
            return
        if event.kind == EventKind.STEP_STARTED:
            self.summary.hide()
            for saved_step in self.steps.values():
                saved_step.status("已结束")
            new_step = StepCard(len(self.steps) + 1)
            if self.turn.status == TurnStatus.RUNNING:
                new_step.status("思考中")
            self.steps[event.step_id] = new_step
            self.agent_body.insertWidget(self.agent_body.count() - 1, new_step)
            return
        step = self.steps.get(event.step_id)
        if event.kind in TERMINAL_EVENTS:
            self.summary.show()
            state = {
                EventKind.TURN_COMPLETED: "已完成",
                EventKind.TURN_FAILED: "失败",
                EventKind.TURN_CANCELLED: "已停止",
                EventKind.TURN_LIMIT_EXCEEDED: "达到上限",
                EventKind.TURN_INTERRUPTED: "结果未知",
            }[event.kind]
            if step:
                step.status(state)
            for saved in self.steps.values():
                saved.heading.set_active(False)
                for tool in saved.tools.values():
                    if tool.state in {"执行中", "等待审批"}:
                        tool.state = "未确认完成"
                        tool.refresh()
            duration = self.turn.duration_s
            time_text = f"{duration:.1f} 秒" if duration < 60 else f"{duration / 60:.1f} 分钟"
            edited = "已编辑" if self.turn.file_metrics_complete else "至少已编辑"
            self.summary.setText(
                f"{state}  ·  用时 {time_text}  ·  {edited} {len(self.turn.edited_files)} 个文件"
                f"  ·  模型 {self.turn.model_calls} 次 / 工具 {self.turn.tool_calls} 次"
                f"\n上下文估算 {self.turn.context_tokens_estimated} Token"
                f"  ·  压缩 {self.turn.context_compactions} 次"
                + (
                    "\n文件统计不完整，请检查项目改动。"
                    if not self.turn.file_metrics_complete
                    else ""
                )
                + ("\n" + event.text if event.kind != EventKind.TURN_COMPLETED else "")
            )
            self.summary.setToolTip(
                "用时包含审批等待；文件数为成功应用补丁和原生命令改动的不同项目文件。"
                "包含新增和删除，重复修改只计一次；过滤目录及工作区外文件不纳入统计。"
            )
            return
        if step is None:
            return
        if event.kind == EventKind.MODEL_DELTA:
            step.output += event.text
            step.body.setText(step.output)
            step.body.show()
            step.status("正在输出")
        elif event.kind == EventKind.MODEL_RESPONDED:
            step.status("模型已响应")
        elif event.kind == EventKind.TOOL_STARTED and event.call_id:
            name, _, arguments = event.text.partition("\n")
            tool = ToolCard(name, arguments)
            step.tools[event.call_id] = tool
            step.layout_box.addWidget(tool)
            step.status("调用工具")
        elif event.call_id and event.call_id in step.tools:
            tool = step.tools[event.call_id]
            if event.kind == EventKind.TOOL_FINISHED:
                title, _, raw = event.text.partition("\n")
                tool.state = "成功" if title.endswith(" · 成功") else "失败"
                try:
                    tool.result = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
                except ValueError:
                    tool.result = raw
                step.status("工具已返回")
            elif event.kind == EventKind.DIFF_PREVIEW:
                tool.diff = event.text
            elif event.kind == EventKind.COMMAND_OUTPUT:
                channel, _, output = event.text.partition("\n")
                tool.log = (tool.log + ("[stderr] " if channel == "stderr" else "") + output)[
                    -32_000:
                ]
            elif event.kind == EventKind.APPROVAL_REQUESTED:
                request = json.loads(event.text)
                tool.state = "等待审批"
                tool.approval = request["summary"]
                step.status("等待审批")
            elif event.kind == EventKind.APPROVAL_RESOLVED:
                request = json.loads(event.text)
                tool.approval += (
                    "\n完全访问：自动执行"
                    if request.get("automatic")
                    else "\n审批结果：" + request["state"]
                )
                tool.state = "执行中" if request["state"] == "已批准" else "审批未通过"
                step.status("处理审批结果")
            tool.refresh()

    def toPlainText(self) -> str:
        parts = ["你", self.turn.prompt, "MiniCodex"]
        for step in self.steps.values():
            parts.extend([step.heading.text(), step.output])
            for tool in step.tools.values():
                parts.extend([tool.toggle.text(), tool.details.toPlainText()])
        return "\n".join([*parts, self.summary.text()])


class ConversationView(QScrollArea):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("conversation")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("conversationContent")
        self.stack = QVBoxLayout(content)
        self.stack.setContentsMargins(10, 22, 22, 8)
        self.stack.setSpacing(20)
        self.stack.addStretch()
        self.setWidget(content)
        self._following = True
        self.verticalScrollBar().rangeChanged.connect(self.follow_range)
        self.verticalScrollBar().sliderMoved.connect(self.follow_position)
        self.verticalScrollBar().actionTriggered.connect(self.manual_scroll)
        self.cards: dict[str, TurnCard] = {}
        self.welcome = text_label("", "welcome")
        self.stack.insertWidget(0, self.welcome)
        self.welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.welcome.setMinimumHeight(260)

    def load(self, thread: ThreadState | None) -> None:
        self.stop_thinking()
        for card in self.cards.values():
            self.stack.removeWidget(card)
            card.deleteLater()
        self.cards.clear()
        self.welcome.setText(
            "从一个任务开始\n\n打开项目，理解代码、修复问题或运行测试。\n"
            "每个步骤的回答和工具调用会一起显示在这里。"
        )
        self.welcome.setVisible(thread is None or not thread.turns)
        if thread:
            for turn in thread.turns:
                self.add_turn(turn)
                for event in turn.events:
                    self.cards[turn.turn_id].consume(event)
        self.scroll_to_bottom()

    def stop_thinking(self) -> None:
        for card in self.cards.values():
            for step in card.steps.values():
                step.heading.set_active(False)

    def add_turn(self, turn: TurnState) -> None:
        self.welcome.hide()
        card = TurnCard(turn)
        self.cards[turn.turn_id] = card
        self.stack.insertWidget(self.stack.count() - 1, card)
        self.scroll_to_bottom()

    def consume(self, event: RuntimeEvent) -> None:
        card = self.cards.get(event.turn_id)
        if card:
            card.consume(event)
        if self._following:
            self.scroll_to_bottom()

    def scroll_to_bottom(self) -> None:
        self._following = True
        QTimer.singleShot(0, self.bottom)

    def bottom(self) -> None:
        if self._following:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def follow_range(self, minimum: int, maximum: int) -> None:
        self.bottom()

    def follow_position(self, position: int) -> None:
        self._following = self.verticalScrollBar().maximum() - position < 40

    def manual_scroll(self, action: int) -> None:
        self._following = False
        QTimer.singleShot(0, self.check_position)

    def check_position(self) -> None:
        self.follow_position(self.verticalScrollBar().value())

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.angleDelta().y() > 0:
            self._following = False
        super().wheelEvent(event)
        if event.angleDelta().y() < 0:
            self.follow_position(self.verticalScrollBar().value())

    def toPlainText(self) -> str:
        return "\n\n".join(card.toPlainText() for card in self.cards.values())
