"""Opt-in Docker Desktop tests through the real Windows CLI/process execution chain."""

import json
import os
import uuid
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt

from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.events import EventKind
from minicodex.backend.core.history import validate_history
from minicodex.backend.core.models import (
    ModelResponse,
    ThreadState,
    ToolCall,
    TurnState,
    TurnStatus,
)
from minicodex.backend.core.runtime import AgentRuntime
from minicodex.backend.execution import commands
from minicodex.backend.execution.approval import ApprovalManager
from minicodex.backend.execution.workspace import Workspace
from minicodex.backend.llm.fake import FakeLLMProvider
from minicodex.frontend.ui.main_window import MainWindow
from minicodex.frontend.ui.settings_dialog import SettingsDialog

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def docker_engine(tmp_path_factory):
    if os.environ.get("MINICODEX_DOCKER_TESTS") != "1":
        pytest.skip("Set MINICODEX_DOCKER_TESTS=1 to require real Docker Desktop tests")
    if os.name != "nt":
        pytest.fail("This suite verifies the production Windows process runner")
    root = tmp_path_factory.mktemp("docker-engine")
    try:
        docker = commands.executable("docker")
    except ValueError as error:
        pytest.fail(str(error))

    def cli(*args, timeout=30):
        return commands.run_native([docker, *args], root, timeout, Event())

    info = cli("info", "--format", "{{.OSType}}")
    assert info["exit_code"] == 0 and info["stdout"].strip() == "linux", info
    base = os.environ.get("MINICODEX_DOCKER_IMAGE", "python:3.12-slim")
    inspected = cli("image", "inspect", "--format", "{{.Id}}", base)
    assert inspected["exit_code"] == 0, "Prepare the local Python image before running: " + base
    tag = "minicodex-sandbox-test-" + uuid.uuid4().hex + ":local"
    (root / "Dockerfile").write_text(
        f"FROM {base}\n"
        "ENV MINICODEX_IMAGE_DEFAULT=present\n"
        'VOLUME ["/minicodex-test-volume"]\n'
        'ENTRYPOINT ["python", "-c", "raise RuntimeError(\'unapproved entrypoint\')"]\n'
        'HEALTHCHECK --interval=1s CMD ["python", "-c", "raise RuntimeError(\'healthcheck\')"]\n',
        encoding="utf-8",
    )
    try:
        built = cli("build", "--pull=false", "--network=none", "--tag", tag, str(root), timeout=120)
        assert built["exit_code"] == 0, built
        yield SimpleNamespace(docker=docker, image=tag, cli=cli)
    finally:
        # Only this fixture's unique tag is removed; never prune user images or volumes.
        cli("image", "rm", tag)


@pytest.fixture
def sandbox(docker_engine, tmp_path, monkeypatch):
    engine = docker_engine
    root = tmp_path / "中文 project with spaces"
    root.mkdir()
    case = SimpleNamespace(root=root, configs=[], names=[], snapshots=[], volumes=[], states=[])
    original_executor = commands.DockerExecutor

    def tracked_runner(argv, cwd, timeout, cancel, on_output=None):
        if argv[1] == "create":
            case.names.append(argv[argv.index("--name") + 1])
            mount = argv[argv.index("--mount") + 1]
            case.snapshots.append(Path(mount.split("src=", 1)[1].split(",dst=", 1)[0]))
        result = commands.run_native(argv, cwd, timeout, cancel, on_output)
        if argv[1] == "inspect" and "{{json .State}}" in argv and result["exit_code"] == 0:
            case.states.append(json.loads(result["stdout"]))
        if argv[1] == "create" and result["exit_code"] == 0:
            inspected = engine.cli("inspect", case.names[-1])
            assert inspected["exit_code"] == 0, inspected
            config = json.loads(inspected["stdout"])[0]
            case.configs.append(config)
            case.volumes.extend(m["Name"] for m in config["Mounts"] if m["Type"] == "volume")
        return result

    monkeypatch.setattr(commands, "DockerExecutor", lambda: original_executor(tracked_runner))

    def run(source, *, cwd=".", timeout=15, cancel=None, on_output=None):
        workspace = Workspace(root)
        event = cancel or Event()
        plan = commands.prepare_command(
            {"argv": ["python", "-c", source], "cwd": cwd, "timeout_s": timeout},
            workspace,
            event,
            "docker",
            engine.image,
            timeout,
        )
        return commands.run_plan(plan, workspace, event, on_output)

    case.run = run
    try:
        yield case
    finally:
        leftovers = []
        for name in case.names:
            inspected = engine.cli("inspect", name)
            absent = (
                inspected["exit_code"] != 0
                and "no such" in inspected["stderr"].lower()
                and name in inspected["stderr"]
            )
            if not absent:
                leftovers.append(name)
                engine.cli("rm", "--force", "--volumes", name)
        for volume in case.volumes:
            inspected = engine.cli("volume", "inspect", volume)
            if inspected["exit_code"] == 0:
                leftovers.append(volume)
                engine.cli("volume", "rm", volume)
        assert not leftovers, f"Sandbox leaked containers or anonymous volumes: {leftovers}"
        assert all(not snapshot.exists() for snapshot in case.snapshots)


def test_real_isolation_policy_and_filtered_mount(sandbox, monkeypatch):
    monkeypatch.setenv("MINICODEX_HOST_SECRET", "host-only-test-value")
    for name in (".env", ".env.local", ".ssh/id_rsa", "nested/key.pem", ".git/config"):
        path = sandbox.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private fixture", encoding="utf-8")
    (sandbox.root / "visible.txt").write_text("allowed", encoding="utf-8")
    result = sandbox.run("""
import json, os, socket
from pathlib import Path

status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines()
              if ':' in line)
assert os.getuid() == 65534 and os.getgid() == 65534
assert int(status['CapEff'].strip(), 16) == 0
assert status['NoNewPrivs'].strip() == '1'
root = next(line.split() for line in Path('/proc/mounts').read_text().splitlines()
            if line.split()[1] == '/')
assert 'ro' in root[3].split(',')
assert Path('/sys/fs/cgroup/memory.max').read_text().strip() == str(512 * 1024 * 1024)
assert Path('/sys/fs/cgroup/pids.max').read_text().strip() == '64'
quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
assert int(quota) == int(period)
assert os.environ.get('MINICODEX_HOST_SECRET') is None
assert os.environ['MINICODEX_IMAGE_DEFAULT'] == 'present'
assert not Path('/var/run/docker.sock').exists()
assert sorted(str(p.relative_to('/workspace')) for p in Path('/workspace').rglob('*')
              if p.is_file()) == ['visible.txt']
Path('/tmp/writable').write_text('tmpfs works')
try:
    socket.create_connection(('1.1.1.1', 53), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('Sandbox has external networking')
print(json.dumps({'uid': os.getuid(), 'isolated': True}))
""")
    assert result["state"] == "completed" and result["exit_code"] == 0, result
    assert json.loads(result["stdout"])["isolated"]
    config = sandbox.configs[-1]
    limits = config["HostConfig"]
    assert config["Config"]["User"] == "65534:65534"
    assert config["Config"]["Healthcheck"]["Test"] == ["NONE"]
    assert config["Config"]["Labels"]["com.minicodex.sandbox"] == "1"
    assert limits["NetworkMode"] == "none" and limits["ReadonlyRootfs"]
    assert limits["CapDrop"] == ["ALL"]
    assert "no-new-privileges" in limits["SecurityOpt"]
    assert limits["Memory"] == limits["MemorySwap"] == 512 * 1024 * 1024
    assert limits["NanoCpus"] == 1_000_000_000 and limits["PidsLimit"] == 64
    assert "nosuid" in limits["Tmpfs"]["/tmp"] and "nodev" in limits["Tmpfs"]["/tmp"]
    bind = next(mount for mount in config["Mounts"] if mount["Type"] == "bind")
    assert bind["Destination"] == "/workspace" and bind["RW"]
    assert sandbox.volumes, "Fixture must exercise anonymous-volume removal"


def test_real_workspace_changes_are_discarded_and_nested_cwd_works(sandbox):
    folder = sandbox.root / "nested dir"
    folder.mkdir()
    (folder / "edit.txt").write_text("original", encoding="utf-8")
    (folder / "delete.txt").write_text("keep", encoding="utf-8")
    result = sandbox.run(
        "from pathlib import Path; assert Path.cwd().as_posix() == '/workspace/nested dir';"
        "Path('edit.txt').write_text('changed'); Path('delete.txt').unlink();"
        "Path('created.txt').write_text('container only'); print('copy verified')",
        cwd="nested dir",
    )
    assert result["exit_code"] == 0, result
    assert (folder / "edit.txt").read_text(encoding="utf-8") == "original"
    assert (folder / "delete.txt").read_text(encoding="utf-8") == "keep"
    assert not (folder / "created.txt").exists()
    assert not result.get("edited_files")


def test_real_exit_code_streams_and_image_entrypoint_override(sandbox):
    output = []
    result = sandbox.run(
        "import sys; print('stdout marker',flush=True);"
        "print('stderr marker',file=sys.stderr,flush=True); sys.exit(7)",
        on_output=lambda channel, text: output.append((channel, text)),
    )
    assert result["state"] == "completed" and result["exit_code"] == 7, result
    assert any(channel == "stdout" and "stdout marker" in text for channel, text in output)
    assert any(channel == "stderr" and "stderr marker" in text for channel, text in output)


def test_real_large_output_is_drained_and_bounded(sandbox):
    result = sandbox.run("import sys; sys.stdout.write('x'*200000); sys.stderr.write('y'*200000)")
    assert result["exit_code"] == 0 and result["truncated"], result
    assert len(result["stdout"].encode()) == commands.OUTPUT_BYTES
    assert len(result["stderr"].encode()) == commands.OUTPUT_BYTES


@pytest.mark.parametrize("stop", ["normal_exit", "cancelled", "timed_out"])
def test_real_child_processes_and_container_removed_before_return(sandbox, stop):
    cancel = Event()
    ready = Event()

    def on_output(channel, text):
        if "READY" in text:
            ready.set()
            if stop == "cancelled":
                cancel.set()

    source = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        "print('READY',flush=True);" + ("time.sleep(60)" if stop != "normal_exit" else "")
    )
    result = sandbox.run(
        source,
        timeout=2 if stop == "timed_out" else 15,
        cancel=cancel,
        on_output=on_output,
    )
    assert ready.is_set(), result
    assert result["state"] == ("completed" if stop == "normal_exit" else stop), result
    assert result["cleanup_verified"] and not result["effects_unknown"]
    assert result["exit_code"] == (0 if stop == "normal_exit" else None)


def test_real_prepare_does_not_pull_a_missing_image(sandbox, docker_engine):
    missing = "minicodex-missing-" + uuid.uuid4().hex + ":never-pull"
    with pytest.raises(ValueError, match="不会自动下载"):
        commands.prepare_command(
            {"argv": ["python", "-c", "print('must not run')"]},
            Workspace(sandbox.root),
            Event(),
            "docker",
            missing,
            15,
        )
    assert docker_engine.cli("image", "inspect", missing)["exit_code"] != 0
    assert not sandbox.names


def test_real_pid_limit_prevents_fork_exhaustion(sandbox):
    result = sandbox.run("""
import errno, json, os, signal, time
children = []
blocked = False
try:
    for _ in range(100):
        try:
            pid = os.fork()
        except OSError as error:
            assert error.errno == errno.EAGAIN, error
            blocked = True
            break
        if pid == 0:
            time.sleep(30)
            os._exit(0)
        children.append(pid)
    assert blocked and len(children) < 64
    print(json.dumps({'blocked': blocked, 'children': len(children)}))
finally:
    for pid in children:
        os.kill(pid, signal.SIGKILL)
    for pid in children:
        os.waitpid(pid, 0)
""")
    assert result["exit_code"] == 0, result
    assert json.loads(result["stdout"])["blocked"]


def test_real_memory_limit_kills_container_without_leaking_resources(sandbox):
    result = sandbox.run("data = bytearray(768 * 1024 * 1024); print(len(data))")
    assert result["state"] == "completed" and result["exit_code"] == 137, result
    assert sandbox.states[-1]["OOMKilled"] is True


@pytest.mark.parametrize("decision", ["approve", "deny", "edit_project", "edit_arguments"])
async def test_real_runtime_approval_receipts_and_file_metrics(sandbox, docker_engine, decision):
    path = sandbox.root / "source.txt"
    path.write_text("original", encoding="utf-8")
    call = ToolCall(
        "docker-command", "run_command", {"argv": ["python", "-c", "print('approved')"]}
    )
    provider = FakeLLMProvider(
        [ModelResponse(tool_calls=(call,)), ModelResponse(text="Task finished")], delay_s=0
    )
    manager = ApprovalManager()
    thread = ThreadState(sandbox.root)
    turn = TurnState(thread.thread_id)
    events = []

    async def publish(event):
        events.append(event)
        if event.kind == EventKind.APPROVAL_REQUESTED:
            request = json.loads(event.text)
            assert request["mode"] == "docker" and "sha256:" in request["summary"]
            if decision == "edit_project":
                path.write_text("user edit after approval", encoding="utf-8")
            if decision == "edit_arguments":
                call.arguments["argv"][2] = "print('changed after approval')"
            await manager.respond(request["approval_id"], turn.turn_id, decision != "deny")

    runtime = AgentRuntime(
        provider,
        settings=ModelSettings(execution_mode="docker", docker_image=docker_engine.image),
        approvals=manager,
    )
    await runtime.run_turn(thread, turn, "Run a sandbox command", Event(), publish)
    validate_history(thread.history)
    assert turn.status == TurnStatus.COMPLETED
    assert not manager.pending and not turn.edited_files
    receipt = json.loads(
        next(message.content for message in thread.history if message.role == "tool")
    )
    assert receipt["success"] is (decision == "approve")
    assert bool(sandbox.names) is (decision == "approve")
    assert path.read_text(encoding="utf-8") == (
        "user edit after approval" if decision == "edit_project" else "original"
    )


@pytest.mark.parametrize("level", ["default", "full"])
def test_real_gui_environment_selection_routes_commands_to_docker(
    sandbox, docker_engine, qtbot, tmp_path, monkeypatch, level
):
    source = sandbox.root / "source.txt"
    source.write_text("original", encoding="utf-8")
    provider = FakeLLMProvider(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        "gui-docker-command",
                        "run_command",
                        {
                            "argv": [
                                "python",
                                "-c",
                                "from pathlib import Path;"
                                "Path('source.txt').write_text('container');"
                                "print('GUI Docker verified')",
                            ]
                        },
                    ),
                )
            ),
            ModelResponse(text="GUI Docker task completed"),
        ],
        delay_s=0,
    )
    facade = ApplicationFacade(provider, data_dir=tmp_path / "gui-client-data")
    window = MainWindow(facade)
    qtbot.addWidget(window)
    window.show()

    def select_docker(dialog):
        dialog.execution_environment.setCurrentIndex(
            dialog.execution_environment.findData("docker")
        )
        dialog.docker_image.setText(docker_engine.image)
        dialog.save()
        return dialog.result()

    monkeypatch.setattr(SettingsDialog, "exec", select_docker)
    try:
        window.open_workspace(sandbox.root)
        window.configure_model()
        assert facade.settings.permission_level == "readonly"
        assert facade.settings.execution_environment == "docker"
        index = window.permission_picker.findData(level)
        window.permission_picker.setCurrentIndex(index)
        window.permission_picker.activated.emit(index)
        assert facade.settings.execution_mode == "docker"
        window.prompt.setPlainText("Run a command in the chosen Docker environment")
        qtbot.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
        if level == "default":
            qtbot.waitUntil(lambda: window._approval_id is not None, timeout=15000)
            qtbot.mouseClick(window.approve_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: not facade.is_running, timeout=30000)
        assert window.status_label.text() == "已完成"
        receipt = json.loads(
            next(message.content for message in facade.thread.history if message.role == "tool")
        )
        result = json.loads(receipt["result"])
        assert receipt["success"] and result["platform"] == "Docker Linux"
        assert sandbox.names and source.read_text(encoding="utf-8") == "original"
        turn = facade.thread.turns[-1]
        assert not turn.edited_files
        requested = [event for event in turn.events if event.kind == EventKind.APPROVAL_REQUESTED]
        assert len(requested) == (1 if level == "default" else 0)
        assert "GUI Docker verified" in window.chat.toPlainText()
    finally:
        window.close()
