import os
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication


def configure_fonts(app: QApplication) -> None:
    """Windows offscreen Qt has no automatic font discovery; load system fonts."""
    if not QFontDatabase.families() and os.name == "nt":
        fonts = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts"
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "arial.ttf"):
            path = fonts / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    families = QFontDatabase.families()
    for family in ("Microsoft YaHei UI", "Segoe UI", "Arial"):
        if family in families:
            app.setFont(QFont(family, 10))
            break
