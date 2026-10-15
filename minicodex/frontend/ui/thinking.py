import math

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QHideEvent,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QPen,
    QShowEvent,
)
from PySide6.QtWidgets import QLabel


class ThinkingLabel(QLabel):
    """A model-wait indicator; animation frames never enter runtime/history."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setObjectName("stepTitle")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setContentsMargins(9, 0, 9, 0)
        self.setMinimumHeight(26)
        self.active = False
        self.phase = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.advance)

    def set_active(self, active: bool) -> None:
        if self.active == active:
            return
        self.active = active
        if active:
            self.phase = 0.0
            self.setToolTip("正在等待模型响应")
            if self.isVisible():
                self.timer.start()
        else:
            self.timer.stop()
            self.setToolTip("")
        self.update()

    def advance(self) -> None:
        self.phase = (self.phase + 0.025) % 1.0
        self.update()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self.active:
            self.timer.start()

    def hideEvent(self, event: QHideEvent) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        if not self.active:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.fontMetrics().horizontalAdvance(self.text())
        capsule = QRectF(0.5, 0.5, min(self.width() - 1, width + 53), self.height() - 1)
        painter.setPen(QPen(QColor("#345148"), 1))
        painter.setBrush(QColor("#23372f"))
        painter.drawRoundedRect(capsule, 8, 8)
        sweep = (self.phase * 2.4 - 0.7) * (width + 20)
        gradient = QLinearGradient(sweep - 50, 0, sweep + 50, 0)
        gradient.setColorAt(0, QColor("#86b69d"))
        gradient.setColorAt(0.5, QColor("#e1ffed"))
        gradient.setColorAt(1, QColor("#86b69d"))
        painter.setPen(QPen(gradient, 1))
        painter.drawText(
            QRectF(9, 0, width + 1, self.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.text(),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(3):
            wave = (math.sin(self.phase * math.tau - index * 0.8) + 1) / 2
            color = QColor("#a2e9c2")
            color.setAlphaF(0.3 + wave * 0.7)
            painter.setBrush(color)
            radius = 1.6 + wave * 0.5
            painter.drawEllipse(
                QRectF(
                    width + 20 + index * 9 - radius,
                    self.height() / 2 - radius - wave,
                    radius * 2,
                    radius * 2,
                )
            )
        painter.end()
