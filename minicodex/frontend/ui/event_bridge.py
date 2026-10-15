from PySide6.QtCore import QObject, QTimer, Signal

from minicodex.backend.application.facade import ApplicationFacade


class EventBridge(QObject):
    event_received = Signal(object)

    def __init__(self, facade: ApplicationFacade, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.facade = facade
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.flush)
        self.timer.start()

    def flush(self) -> None:
        for event in self.facade.drain_events():
            self.event_received.emit(event)
