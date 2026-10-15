import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

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
                result["stdout"] = json.dumps({"Running": False, "Status": "exited", "ExitCode": 7})
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


@pytest.fixture
def docker_case(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("print('original')", encoding="utf-8")
    workspace = Workspace(project)
    _, digest = manifest(workspace, Event())
    image = "sha256:" + "c" * 64
    case = SimpleNamespace(
        workspace=workspace,
        plan=CommandPlan(("python", "a.py"), ".", 3, "docker", image, digest, "approved"),
        calls=[],
        behaviors={},
        present=False,
        snapshot=None,
        name="",
        copied=[],
        cancel_on_start=False,
    )
    monkeypatch.setattr(
        "minicodex.backend.execution.commands.executable", lambda value: "docker.exe"
    )

    def runner(argv, cwd, timeout, cancel, on_output=None):
        stage = argv[1]
        if stage == "inspect":
            stage = "state" if "{{json .State}}" in argv else "absence"
        case.calls.append((stage, argv, timeout, cancel))
        result = {
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "state": "completed",
            "cleanup_verified": True,
        }
        if stage == "create":
            case.present = True
            case.name = argv[argv.index("--name") + 1]
            mount = argv[argv.index("--mount") + 1]
            case.snapshot = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
            case.copied = sorted(
                path.relative_to(case.snapshot).as_posix()
                for path in case.snapshot.rglob("*")
                if path.is_file()
            )
        if stage == "start" and case.cancel_on_start:
            cancel.set()
        behavior = case.behaviors.get(stage)
        if isinstance(behavior, Exception):
            raise behavior
        if callable(behavior):
            return {**result, **behavior()}
        if behavior is not None:
            return {**result, **behavior}
        if stage == "info":
            result["stdout"] = "linux\n"
        elif stage == "image":
            result["stdout"] = image
        elif stage == "start":
            result["stdout"] = "command output"
            if on_output:
                on_output("stdout", result["stdout"])
        elif stage == "state":
            result["stdout"] = json.dumps(
                {"Running": False, "Status": "exited", "ExitCode": 0, "Error": ""}
            )
        elif stage == "rm":
            case.present = False
        elif stage == "absence":
            result.update(exit_code=1, stderr="Error: No such object: " + case.name)
        return result

    case.runner = runner
    case.executor = DockerExecutor(runner)
    return case


@pytest.mark.parametrize(
    ("stage", "response", "message"),
    [
        ("info", {"exit_code": 1}, "Linux 后端"),
        ("info", {"stdout": "windows"}, "Linux 后端"),
        ("image", {"exit_code": 1}, "镜像未就绪"),
        ("image", {"stdout": "sha256:" + "g" * 64}, "镜像未就绪"),
        ("image", {"stdout": "sha256:abc"}, "镜像未就绪"),
        ("image", {"stdout": "sha256:" + "d" * 64}, "固定 ID 不一致"),
    ],
)
def test_preflight_fails_closed_without_creating_or_pulling(docker_case, stage, response, message):
    case = docker_case
    case.behaviors[stage] = response
    with pytest.raises(ValueError, match=message):
        case.executor.run(case.plan, case.workspace, Event())
    assert {call[0] for call in case.calls} <= {"info", "image"}


@pytest.mark.parametrize("mutation", ["edit", "add", "delete"])
def test_workspace_approval_invalidated_before_container_creation(docker_case, mutation):
    case = docker_case
    source = case.workspace.root / "a.py"
    if mutation == "edit":
        source.write_text("user edit", encoding="utf-8")
    elif mutation == "add":
        (case.workspace.root / "new.txt").touch()
    else:
        source.unlink()
    with pytest.raises(ValueError, match="审批后项目内容已变化"):
        case.executor.run(case.plan, case.workspace, Event())
    assert not any(call[0] == "create" for call in case.calls)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_nonpositive_or_nonfinite_timeouts_never_invoke_docker(docker_case, monkeypatch, timeout):
    case = docker_case
    monkeypatch.setattr("minicodex.backend.execution.commands.run_native", case.runner)
    with pytest.raises(ValueError, match="正的有限数值"):
        prepare_command(
            {"argv": ["python"], "timeout_s": timeout},
            case.workspace,
            Event(),
            "docker",
            case.plan.image,
            5,
        )
    assert not case.calls


@pytest.mark.parametrize("cwd", ["../outside", "/workspace", "C:\\Windows", ".env", "missing"])
def test_unsafe_or_missing_cwd_rejected_before_docker(docker_case, monkeypatch, cwd):
    case = docker_case
    monkeypatch.setattr("minicodex.backend.execution.commands.run_native", case.runner)
    with pytest.raises((OSError, ValueError)):
        prepare_command(
            {"argv": ["python"], "cwd": cwd},
            case.workspace,
            Event(),
            "docker",
            case.plan.image,
            5,
        )
    assert not case.calls


def test_cancelled_before_preflight_never_contacts_docker(docker_case):
    case = docker_case
    cancel = Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        case.executor.run(case.plan, case.workspace, cancel)
    assert not case.calls


def test_cancelled_during_snapshot_never_creates_container(docker_case, monkeypatch):
    case = docker_case
    cancel = Event()
    original_write = Path.write_bytes
    copied_paths = []

    def write(path, data):
        copied_paths.append(path.parent)
        size = original_write(path, data)
        cancel.set()
        return size

    monkeypatch.setattr(Path, "write_bytes", write)
    with pytest.raises(InterruptedError):
        case.executor.run(case.plan, case.workspace, cancel)
    assert not any(call[0] == "create" for call in case.calls)
    assert copied_paths and not copied_paths[0].exists()


@pytest.mark.parametrize(
    ("stage", "response", "exception"),
    [
        ("create", {"exit_code": 125}, ValueError),
        ("create", OSError("create interrupted"), OSError),
        ("start", OSError("attach failed"), OSError),
        ("state", OSError("daemon lost"), OSError),
    ],
)
def test_partial_create_and_start_failures_still_remove_container_and_copy(
    docker_case, stage, response, exception
):
    case = docker_case
    case.behaviors[stage] = response
    with pytest.raises(exception):
        case.executor.run(case.plan, case.workspace, Event())
    assert [call[0] for call in case.calls][-2:] == ["rm", "absence"]
    assert not case.present and not case.snapshot.exists()


@pytest.mark.parametrize(
    "response",
    [
        {"exit_code": 1, "stdout": "", "stderr": "daemon unavailable"},
        {"stdout": "not json"},
        {"stdout": "null"},
        {"stdout": "[]"},
        {"stdout": json.dumps({"Running": True, "Status": "running", "ExitCode": 0})},
        {"stdout": json.dumps({"Running": False, "Status": "created", "ExitCode": 0})},
        {"stdout": json.dumps({"Running": False, "Status": "exited"})},
        {"stdout": json.dumps({"Running": False, "Status": "exited", "ExitCode": False})},
        {
            "stdout": json.dumps(
                {"Running": False, "Status": "exited", "ExitCode": 0, "Error": "OCI failure"}
            )
        },
    ],
)
def test_unconfirmed_exit_never_becomes_success_and_always_cleans_up(docker_case, response):
    case = docker_case
    case.behaviors["state"] = response
    with pytest.raises(RuntimeError, match="退出状态未确认.*禁止自动重放"):
        case.executor.run(case.plan, case.workspace, Event())
    assert not case.present and not case.snapshot.exists()


@pytest.mark.parametrize("stop", ["cancelled", "timed_out"])
def test_cancel_and_timeout_cleanup_uses_independent_event(docker_case, stop):
    case = docker_case
    cancel = Event()
    case.cancel_on_start = stop == "cancelled"
    case.behaviors["start"] = {"state": stop, "exit_code": 137}
    result = case.executor.run(case.plan, case.workspace, cancel)
    assert result["state"] == stop and result["exit_code"] is None
    cleanup_calls = [call for call in case.calls if call[0] in {"state", "rm", "absence"}]
    assert all(call[2] == 15 and call[3] is not cancel for call in cleanup_calls)
    assert all(not call[3].is_set() for call in cleanup_calls)
    assert not case.present and not case.snapshot.exists()


@pytest.mark.parametrize(
    "response",
    [
        {"exit_code": 1, "stderr": "daemon unavailable"},
        {"exit_code": 1, "stderr": "No such object: different-container"},
        {"exit_code": 0, "stdout": "container-still-exists"},
        OSError("cannot verify daemon"),
    ],
)
def test_cleanup_requires_absence_of_the_exact_container(docker_case, response):
    case = docker_case
    case.behaviors["absence"] = response
    with pytest.raises(RuntimeError, match="清理未确认.*禁止自动重放") as failure:
        case.executor.run(case.plan, case.workspace, Event())
    assert case.name in str(failure.value)
    assert not case.snapshot.exists()


def test_rm_exception_still_checks_absence_and_does_not_mask_success(docker_case):
    case = docker_case
    case.behaviors["rm"] = RuntimeError("rm transport ended after daemon removed container")
    result = case.executor.run(case.plan, case.workspace, Event())
    assert result["exit_code"] == 0
    assert [call[0] for call in case.calls][-2:] == ["rm", "absence"]


def test_cleanup_accepts_docker_29_lowercase_missing_object_message(docker_case):
    case = docker_case
    case.behaviors["absence"] = lambda: {
        "exit_code": 1,
        "stderr": "error: no such object: " + case.name + "\n",
    }
    assert case.executor.run(case.plan, case.workspace, Event())["exit_code"] == 0


@pytest.mark.parametrize("fault", ["different_suffix", "wrong_resource", "query_timeout"])
def test_cleanup_does_not_accept_ambiguous_or_incomplete_absence(docker_case, fault):
    case = docker_case

    def response():
        return {
            "exit_code": 1,
            "state": "timed_out" if fault == "query_timeout" else "completed",
            "stderr": "Error: No such "
            + ("network" if fault == "wrong_resource" else "object")
            + ": "
            + case.name
            + ("-different" if fault == "different_suffix" else ""),
        }

    case.behaviors["absence"] = response
    with pytest.raises(RuntimeError, match="清理未确认"):
        case.executor.run(case.plan, case.workspace, Event())


def test_snapshot_filters_nested_secrets_and_only_mounts_disposable_copy(docker_case):
    case = docker_case
    for secret in (".env.local", ".ssh/id_rsa", "sub/key.pem", "sub/.aws/credentials"):
        path = case.workspace.root / secret
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private fixture", encoding="utf-8")
    output = []
    result = case.executor.run(
        case.plan, case.workspace, Event(), lambda channel, text: output.append((channel, text))
    )
    create = next(call[1] for call in case.calls if call[0] == "create")
    assert create.count("--mount") == 1
    assert case.copied == ["a.py"]
    assert str(case.workspace.root) not in create[create.index("--mount") + 1]
    assert create[create.index("--pull") + 1] == "never"
    assert create[create.index("--cap-drop") + 1] == "ALL"
    assert create[create.index("--security-opt") + 1] == "no-new-privileges"
    assert create[create.index("--memory-swap") + 1] == "512m"
    assert create[create.index("--cpus") + 1] == "1"
    assert create[create.index("--tmpfs") + 1] == "/tmp:rw,nosuid,nodev,size=128m"
    assert create[create.index("--entrypoint") + 1] == "python"
    assert "--no-healthcheck" in create
    assert "--privileged" not in create and "--env-file" not in create
    assert result["workspace_mode"] == "filtered temporary copy; changes discarded"
    assert output == [("stdout", "command output")]
    assert not case.snapshot.exists()
