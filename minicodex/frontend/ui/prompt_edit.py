from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import QPlainTextEdit


class PromptEdit(QPlainTextEdit):
    send_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._composing = False

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            if self._composing:
                event.accept()
                return
            modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
            if modifiers in {
                Qt.KeyboardModifier.NoModifier,
                Qt.KeyboardModifier.ControlModifier,
            }:
                if not event.isAutoRepeat():
                    self.send_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)
