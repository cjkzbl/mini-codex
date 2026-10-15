from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QSizePolicy, QToolButton, QWidget


class SessionRow(QWidget):
    activated = Signal()
    rename_requested = Signal()
    delete_requested = Signal()

    def __init__(self, title: str) -> None:
        super().__init__()
        self.full_title = title
        self.setObjectName("sessionRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(0)
        self.title_button = QPushButton(title)
        self.title_button.setObjectName("sessionTitle")
        self.title_button.setAccessibleName(title)
        self.title_button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title_button.clicked.connect(self.activated)
        layout.addWidget(self.title_button, 1)
        self.more_button = QToolButton()
        self.more_button.setObjectName("sessionMore")
        dots = QPixmap(36, 36)
        dots.setDevicePixelRatio(2)
        dots.fill(Qt.GlobalColor.transparent)
        painter = QPainter(dots)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#c8d2e0"))
        for x in (3, 8, 13):
            painter.drawEllipse(x, 8, 2, 2)
        painter.end()
        self.more_button.setIcon(QIcon(dots))
        self.more_button.setIconSize(QSize(18, 18))
        self.more_button.setFixedWidth(28)
        self.more_button.setAccessibleName("对话操作：" + title)
        self.more_button.setToolTip("对话操作")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self.more_button)
        self.rename_action = self.menu.addAction("重命名")
        self.rename_action.triggered.connect(self.rename_requested)
        self.delete_action = self.menu.addAction("删除对话")
        self.delete_action.triggered.connect(self.delete_requested)
        self.more_button.setMenu(self.menu)
        layout.addWidget(self.more_button)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.title_button.setText(
            self.title_button.fontMetrics().elidedText(
                self.full_title, Qt.TextElideMode.ElideRight, max(0, self.title_button.width() - 12)
            )
        )
