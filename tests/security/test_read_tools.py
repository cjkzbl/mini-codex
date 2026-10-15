import json
import os
import subprocess
from threading import Event

import pytest

from minicodex.backend.core.models import ToolCall
from minicodex.backend.execution.workspace import Workspace
from minicodex.backend.tools.read_only import make_registry
from minicodex.backend.tools.router import ToolRouter


async def execute(tmp_path, name, args):
    return await ToolRouter(make_registry(), Workspace(tmp_path)).execute(
        ToolCall("call", name, args), Event()
    )


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "sub/../../outside.txt",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "\\\\server\\share\\file",
        "file.txt:stream",
        ".git/config",
        ".env",
        ".env.local",
        ".ssh/id_rsa",
        "certificate.pem",
    ],
)
async def test_escape_and_protected_paths_are_denied(tmp_path, path):
    reply = await execute(tmp_path, "read_file", {"path": path})
    assert not reply.success


async def test_unicode_crlf_and_limits_without_modifying_files(tmp_path):
    path = tmp_path / "中文 file.py"
    original = "第一行\r\nreturn 42\r\n最后一行\r\n".encode()
    path.write_bytes(original)
    data = json.loads(
        (
            await execute(
                tmp_path,
                "read_file",
                {
                    "path": path.name,
                    "start_line": 2,
                    "end_line": 2,
                },
            )
        ).content
    )
    assert data["content"] == "2: return 42"
    assert path.read_bytes() == original
    found = json.loads((await execute(tmp_path, "search_code", {"pattern": "return"})).content)
    assert found["matches"][0]["path"] == path.name
    assert found["matches"][0]["line"] == 2


async def test_excluded_dirs_binary_large_output_and_schema(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/config").write_text("SECRET")
    (tmp_path / ".env").write_text("SECRET")
    (tmp_path / "ok.txt").write_text("a" * 30_000)
    (tmp_path / "binary.bin").write_bytes(b"hello\x00binary")
    (tmp_path / "large.txt").write_bytes(b"a" * 1_048_577)
    listing = json.loads((await execute(tmp_path, "list_files", {})).content)
    assert not any(p.startswith((".git", ".env")) for p in listing["files"])
    for path in ("binary.bin", "large.txt"):
        assert not (await execute(tmp_path, "read_file", {"path": path})).success
    read = json.loads((await execute(tmp_path, "read_file", {"path": "ok.txt"})).content)
    assert read["truncated"] and len(read["content"]) <= 16_000
    assert not (await execute(tmp_path, "read_file", {"path": "ok.txt", "evil": True})).success
    assert not (await execute(tmp_path, "list_files", {"max_results": True})).success


async def test_junction_cannot_escape_or_leak_files(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction test")
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("DO NOT READ")
    junction = root / "linked"
    command = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=True,
    )
    assert command.returncode == 0
    reply = await execute(root, "read_file", {"path": "linked/secret.txt"})
    assert not reply.success
    listing = await execute(root, "list_files", {})
    assert "secret.txt" not in listing.content


async def test_actual_open_handle_is_checked_even_if_path_changes(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows final-handle policy")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "allowed.txt").write_text("SAFE")
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET_VALUE")
    original = os.open
    monkeypatch.setattr(os, "open", lambda path, flags, *args, **kwargs: original(secret, flags))
    reply = await execute(root, "read_file", {"path": "allowed.txt"})
    assert not reply.success and "SECRET_VALUE" not in reply.content


def test_schema_snapshot_cannot_mutate_registry():
    registry = make_registry()
    schemas = registry.schemas()
    schemas[0]["function"]["parameters"]["properties"].clear()
    assert "max_results" in registry.schemas()[0]["function"]["parameters"]["properties"]
