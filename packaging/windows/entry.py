import json
import os
import sys
import traceback
from pathlib import Path


def launch() -> int:
    try:
        from minicodex.frontend.desktop import main

        return main()
    except Exception:
        details = traceback.format_exc()
        if "--smoke-test" in sys.argv:
            index = sys.argv.index("--smoke-test")
            report = Path(sys.argv[index + 1])
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps({"passed": False, "error": details}), encoding="utf-8")
        else:
            folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MiniCodex"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "startup-error.log").write_text(details, encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(launch())
