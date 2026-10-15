import os
from threading import Event

import pytest

from minicodex.backend.execution.patch import apply_plan, prepare_patch
from minicodex.backend.execution.windows import locked_file
from minicodex.backend.execution.workspace import Workspace


def patch(path, old="old", new="new"):
    return f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{old}\n+{new}\n"


@pytest.mark.parametrize(
    "encoding,bom",
    [
        ("utf-8", b""),
        ("utf-8-sig", b""),
        ("utf-16-le", b"\xff\xfe"),
        ("utf-16-be", b"\xfe\xff"),
        ("gb18030", b""),
    ],
)
def test_patch_preserves_encoding_bom_crlf_and_user_content(tmp_path, encoding, bom):
    path = tmp_path / "中文 文件.py"
    before = "旧值\r\n用户原有修改\r\n"
    path.write_bytes(bom + before.encode(encoding))
    workspace = Workspace(tmp_path)
    plan = prepare_patch(patch(path.name, "旧值", "新值"), workspace, Event())
    assert path.read_bytes() == bom + before.encode(encoding)
    assert "新值" in plan.diff
    apply_plan(plan, workspace, Event())
    assert path.read_bytes() == bom + "新值\r\n用户原有修改\r\n".encode(encoding)


def test_multifile_create_delete_and_exact_context(tmp_path):
    (tmp_path / "old.txt").write_text("old\n", "utf-8")
    source = (
        "--- a/old.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"
        "--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1,2 @@\n+hello\n+world\n"
    )
    workspace = Workspace(tmp_path)
    plan = prepare_patch(source, workspace, Event())
    apply_plan(plan, workspace, Event())
    assert not (tmp_path / "old.txt").exists()
    assert (tmp_path / "new.txt").read_bytes() == b"hello\nworld\n"


def test_file_changes_after_approval_reject_and_remove_staged_new_file(tmp_path):
    original = tmp_path / "file.txt"
    original.write_bytes(b"old\n")
    workspace = Workspace(tmp_path)
    source = "--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+hello\n" + patch("file.txt")
    plan = prepare_patch(source, workspace, Event())
    original.write_bytes(b"user changed\n")
    with pytest.raises(ValueError, match="变化"):
        apply_plan(plan, workspace, Event())
    assert original.read_bytes() == b"user changed\n"
    assert not (tmp_path / "new.txt").exists()


def test_cancel_mid_patch_rolls_back_before_terminal_result(tmp_path, monkeypatch):
    for name in ("a", "b"):
        (tmp_path / name).write_bytes(b"old\n")
    workspace, cancel = Workspace(tmp_path), Event()
    plan = prepare_patch(patch("a") + patch("b"), workspace, cancel)
    fsync = os.fsync

    def interrupt(fd):
        fsync(fd)
        cancel.set()

    monkeypatch.setattr("minicodex.backend.execution.patch.os.fsync", interrupt)
    with pytest.raises(InterruptedError):
        apply_plan(plan, workspace, cancel)
    assert (tmp_path / "a").read_bytes() == (tmp_path / "b").read_bytes() == b"old\n"


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "C:/outside",
        "/outside",
        "\\\\server\\share",
        ".git/config",
        ".env",
        "x:stream",
        "NUL.txt",
        "trailing.",
        "a/../x",
    ],
)
def test_write_escape_protected_and_windows_aliases_rejected(tmp_path, path):
    with pytest.raises((OSError, ValueError)):
        prepare_patch(patch(path), Workspace(tmp_path), Event())


def test_occupied_file_and_hardlink_never_overwritten(tmp_path):
    target = tmp_path / "a"
    target.write_bytes(b"old\n")
    workspace = Workspace(tmp_path)
    plan = prepare_patch(patch("a"), workspace, Event())
    with locked_file(target):
        with pytest.raises(OSError):
            apply_plan(plan, workspace, Event())
    os.link(target, tmp_path / "alias")
    with pytest.raises(PermissionError, match="硬链接"):
        apply_plan(plan, workspace, Event())
    assert target.read_bytes() == b"old\n"


def test_junction_write_escape_rejected(tmp_path):
    import subprocess

    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "a").write_bytes(b"old\n")
    junction = tmp_path / "junction"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)], check=True, capture_output=True
    )
    try:
        with pytest.raises(PermissionError):
            prepare_patch(patch("junction/a"), Workspace(tmp_path), Event())
        assert (outside / "a").read_bytes() == b"old\n"
    finally:
        junction.rmdir()


def test_patch_no_newline_and_stale_hunk(tmp_path):
    target = tmp_path / "a"
    target.write_bytes(b"old")
    workspace = Workspace(tmp_path)
    source = patch("a") + "\\ No newline at end of file\n"
    apply_plan(prepare_patch(source, workspace, Event()), workspace, Event())
    assert target.read_bytes() == b"new"
    with pytest.raises(ValueError, match="原文"):
        prepare_patch(patch("a"), workspace, Event())
