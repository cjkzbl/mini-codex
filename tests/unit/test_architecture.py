import subprocess
import sys
from pathlib import Path


def test_backend_runs_without_frontend_or_qt(tmp_path):
    """Import every backend module and run a turn with desktop imports unavailable."""
    script = """
import importlib
import importlib.abc
import pkgutil
import sys
import time
from pathlib import Path

class NoDesktopImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'PySide6' or fullname.startswith(('PySide6.', 'minicodex.frontend')):
            raise ImportError(f'Backend requested desktop dependency: {fullname}')

sys.meta_path.insert(0, NoDesktopImports())
import minicodex.backend

for module in pkgutil.walk_packages(minicodex.backend.__path__, 'minicodex.backend.'):
    importlib.import_module(module.name)

from minicodex.backend.application.commands import StartTurn
from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.core.events import EventKind

facade = ApplicationFacade()
try:
    thread = facade.select_workspace(Path(sys.argv[1]))
    facade.start(StartTurn(thread.thread_id, 'List workspace files'))
    events = []
    deadline = time.monotonic() + 10
    while facade.is_running and time.monotonic() < deadline:
        events.extend(facade.drain_events())
        time.sleep(0.01)
    events.extend(facade.drain_events())
    assert not facade.is_running, 'Headless turn timed out'
    assert any(event.kind == EventKind.TURN_COMPLETED for event in events), events
    assert not any(name == 'PySide6' or name.startswith(('PySide6.', 'minicodex.frontend'))
                   for name in sys.modules)
finally:
    assert facade.close(), 'Backend worker failed to stop'
"""
    (tmp_path / "README.md").write_text("# Headless workspace\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
