import os
from pathlib import Path


def default_data_directory() -> Path:
    base = (
        Path(os.environ["LOCALAPPDATA"])
        if os.environ.get("LOCALAPPDATA")
        else Path.home() / ".local" / "share"
    )
    return base / "MiniCodex"
