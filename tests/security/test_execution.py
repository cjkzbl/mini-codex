import ctypes
import sys
import time
from threading import Event, Timer

import pytest

from minicodex.backend.execution.commands import OUTPUT_BYTES, clean_environment, run_native
from minicodex.backend.execution.windows import api


def test_real_command_output_exit_code_and_environment_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("M2_PRIVATE_SECRET", "DO_NOT_FORWARD")
    source = (
        "import os,sys;print(os.environ.get('M2_PRIVATE_SECRET','absent'));"
        "print('error',file=sys.stderr);sys.exit(7)"
    )
    result = run_native([sys.executable, "-c", source], tmp_path, 5, Event())
    assert result["exit_code"] == 7 and result["cleanup_verified"]
    assert "absent" in result["stdout"] and "error" in result["stderr"]
    assert "M2_PRIVATE_SECRET" not in clean_environment()


def test_large_output_drained_but_bounded(tmp_path):
    result = run_native([sys.executable, "-c", "print('x'*200000)"], tmp_path, 5, Event())
    assert result["exit_code"] == 0 and result["truncated"]
    assert len(result["stdout"]) == OUTPUT_BYTES


@pytest.mark.parametrize("stop", ["timeout", "cancel", "normal_exit"])
def test_child_tree_removed_before_return_no_late_effect(tmp_path, stop):
    marker = tmp_path / "late.txt"
    child = f"import time,pathlib;time.sleep(1.2);pathlib.Path({str(marker)!r}).write_text('late')"
    source = (
        "import subprocess,sys,time;"
        + f"p=subprocess.Popen([sys.executable,'-c',{child!r}]);print(p.pid,flush=True);"
        + ("time.sleep(30)" if stop != "normal_exit" else "")
    )
    cancel = Event()
    timer = Timer(0.4, cancel.set)
    if stop == "cancel":
        timer.start()
    try:
        result = run_native(
            [sys.executable, "-c", source], tmp_path, 0.5 if stop == "timeout" else 5, cancel
        )
    finally:
        timer.cancel()
    assert result["cleanup_verified"]
    assert (
        result["state"]
        == {"timeout": "timed_out", "cancel": "cancelled", "normal_exit": "completed"}[stop]
    )
    child_pid = int(result["stdout"].strip())
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    dll.OpenProcess.restype = ctypes.c_void_p
    handle = dll.OpenProcess(0x100000, False, child_pid)
    if handle:
        try:
            assert api().WaitForSingleObject(handle, 1000) == 0
        finally:
            api().CloseHandle(handle)
    time.sleep(1.3)
    assert not marker.exists()


def test_job_assignment_failure_never_starts_target(tmp_path, monkeypatch):
    marker = tmp_path / "must-not-run"
    original = api()

    class FailedAssignment:
        def __getattr__(self, name):
            return getattr(original, name)

        def AssignProcessToJobObject(self, *args):
            ctypes.set_last_error(5)
            return 0

    monkeypatch.setattr("minicodex.backend.execution.windows.api", lambda: FailedAssignment())
    with pytest.raises(OSError):
        run_native(
            [sys.executable, "-c", f"import pathlib;pathlib.Path({str(marker)!r}).touch()"],
            tmp_path,
            3,
            Event(),
        )
    assert not marker.exists()
