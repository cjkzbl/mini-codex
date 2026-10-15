"""Copy dependency license notices next to the redistributable directory build."""

import argparse
import importlib.metadata
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, default=root / "dist" / "MiniCodex")
args = parser.parse_args()
bundle = args.output.resolve()
bundle.relative_to(root)
target = bundle / "THIRD_PARTY_LICENSES"
target.mkdir(parents=True, exist_ok=True)
for name in (
    "PySide6",
    "PySide6_Essentials",
    "PySide6_Addons",
    "shiboken6",
    "pyinstaller",
    "httpx",
    "httpcore",
    "h11",
    "anyio",
    "certifi",
    "idna",
    "jsonschema",
    "jsonschema-specifications",
    "referencing",
    "rpds-py",
    "attrs",
    "typing_extensions",
):
    distribution = importlib.metadata.distribution(name)
    for file in distribution.files or ():
        if any(marker in str(file).lower() for marker in ("license", "copying", "notice")):
            source = Path(distribution.locate_file(file))
            if source.is_file() and source.suffix.lower() in (".txt", ".md", ".rst", ""):
                destination = target / name / Path(str(file))
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
(target / "README.txt").write_text(
    "Dependency notices for this development prototype. Qt libraries remain dynamically linked.\n"
    "See https://doc.qt.io/qtforpython-6/licenses.html before public distribution.\n",
    encoding="utf-8",
)
print(f"License notices: {target}")
