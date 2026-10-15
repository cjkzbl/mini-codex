import json
from threading import Event

import pytest

from minicodex.backend.execution.commands import (
    CommandPlan,
    DockerExecutor,
    manifest,
    prepare_command,
)
from minicodex.backend.execution.workspace import Workspace


@pytest.mark.parametrize("stop", ["completed", "cancelled", "timed_out", "cleanup_unknown"])
def test_docker_isolation_snapshot_exit_and_cleanup_contract(tmp_path, monkeypatch, stop):
    (tmp_path / "a.py").write_text("print('original')", "utf-8")
    (tmp_path / ".env").write_text("SECRET", "utf-8")
    workspace = Workspace(tmp_path)
    _, digest = manifest(workspace, Event())
    calls = []
    present = False
    image_id = "sha256:" + "a" * 64
    monkeypatch.setattr(
        "minicodex.backend.execution.commands.executable", lambda value: "docker.exe"
    )

    def runner(argv, cwd, timeout, cancel, on_output=None):
        nonlocal present
        calls.append(argv)
        result = {
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "state": "completed",
            "cleanup_verified": True,
        }
        if argv[1] == "info":
            result["stdout"] = "linux\n"
        elif argv[1] == "image":
            result["stdout"] = image_id
        elif argv[1] == "create":
            present = True
            mount = argv[argv.index("--mount") + 1]
            from pathlib import Path

            snapshot = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
            assert (snapshot / "a.py").read_text("utf-8") == "print('original')"
            assert not (snapshot / ".env").exists()
            (snapshot / "a.py").write_text("container modification", "utf-8")
            assert argv[argv.index("--network") + 1] == "none"
            assert "--read-only" in argv and "--cap-drop" in argv
            assert argv[argv.index("--user") + 1] == "65534:65534"
            assert argv[argv.index("--memory") + 1] == "512m"
            assert argv[argv.index("--pids-limit") + 1] == "64"
            assert image_id in argv
        elif argv[1] == "start":
            result.update(
                {
                    "state": "completed" if stop == "cleanup_unknown" else stop,
                    "stdout": "test result",
                }
            )
        elif argv[1] == "rm":
            assert not cancel.is_set() and "--force" in argv and "--volumes" in argv
            present = False
        elif argv[1] == "inspect":
            if not present:
                result.update(
                    {
                        "exit_code": 1,
                        "stderr": "daemon unavailable"
                        if stop == "cleanup_unknown"
                        else "No such object: " + argv[-1],
                    }
                )
            else:
                result["stdout"] = json.dumps({"Running": False, "ExitCode": 7})
        return result

    executor = DockerExecutor(runner)
    plan = CommandPlan(("python", "a.py"), ".", 2, "docker", image_id, digest, "fingerprint")
    if stop == "cleanup_unknown":
        with pytest.raises(RuntimeError, match="清理未确认"):
            executor.run(plan, workspace, Event())
    else:
        result = executor.run(plan, workspace, Event())
        assert result["platform"] == "Docker Linux"
        if stop == "completed":
            assert result["exit_code"] == 7
    assert (tmp_path / "a.py").read_text("utf-8") == "print('original')"
    assert any(c[1] == "rm" for c in calls)


def test_docker_missing_fails_closed_before_approval(tmp_path, monkeypatch):
    def absent(value):
        raise ValueError("Docker not installed")

    monkeypatch.setattr("minicodex.backend.execution.commands.executable", absent)
    with pytest.raises(ValueError, match="not installed"):
        prepare_command(
            {"argv": ["python", "test.py"]},
            Workspace(tmp_path),
            Event(),
            "docker",
            "python:3.12-slim",
            5,
        )


def test_docker_approval_pins_local_image_id(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "minicodex.backend.execution.commands.executable", lambda value: "docker.exe"
    )
    image_id = "sha256:" + "b" * 64

    def run(argv, *args):
        return {"exit_code": 0, "stdout": "linux" if argv[1] == "info" else image_id}

    monkeypatch.setattr("minicodex.backend.execution.commands.run_native", run)
    plan = prepare_command(
        {"argv": ["python", "test.py"]}, Workspace(tmp_path), Event(), "docker", "mutable-tag", 5
    )
    assert plan.image == image_id
