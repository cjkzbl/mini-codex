from PySide6.QtCore import QSize


def fit_window(available: QSize, requested: QSize) -> QSize:
    return QSize(
        min(max(640, requested.width()), max(320, available.width() - 24)),
        min(max(480, requested.height()), max(240, available.height() - 24)),
    )
