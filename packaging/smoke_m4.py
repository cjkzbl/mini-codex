"""Isolated installer/persistence/DPI check; never operates on real user data.

Build with build_installer.ps1 -TestBuild first. A separate AppId and shortcut
group keep verification installs distinct from the user's MiniCodex install.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import winreg
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minicodex.backend.application.facade import ApplicationFacade  # noqa: E402
from minicodex.backend.application.settings import ModelSettings  # noqa: E402
from minicodex.backend.persistence.configuration import ConfigurationStore  # noqa: E402

REGISTRY_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    r"\{A80A57C9-4244-4FD3-A166-CA9468D60A4F}_is1"
)


def registered() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY):
            return True
    except FileNotFoundError:
        return False


def run(arguments: list[str], environment: dict[str, str] | None = None) -> None:
    subprocess.run(
        arguments,
        check=True,
        timeout=180,
        env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--installer",
        type=Path,
        default=root / "build/installer-check/MiniCodex-Verification-Setup.exe",
    )
    args = parser.parse_args()
    if args.installer.name != "MiniCodex-Verification-Setup.exe" or registered():
        raise RuntimeError("Use TestBuild; an existing verification install is protected")
    artifacts = root / "artifacts"
    artifacts.mkdir(exist_ok=True)
    report_path = artifacts / "m4-package-smoke.json"
    report_path.unlink(missing_ok=True)
    environment = dict(os.environ)
    environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    environment["QT_QPA_PLATFORM"] = "offscreen"
    group = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"
    group /= "MiniCodex Verification"
    if group.exists():
        raise RuntimeError("An existing verification shortcut group is protected")
    gate_root = root / "build/installer-check"
    gate_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="中文 安装验收 ", dir=gate_root) as folder:
        fixture = Path(folder).resolve()
        if not fixture.is_relative_to(root.resolve()):
            raise RuntimeError("Fixture escaped workspace")
        install = fixture / "客户端"
        data = fixture / "独立数据"
        uninstaller = install / "unins000.exe"
        config = ConfigurationStore(data)
        secret = "synthetic-m4-" + uuid4().hex
        settings = ModelSettings(model="m4-offline-fixture", api_key=secret, max_steps=17)
        results: list[dict[str, object]] = []
        try:
            facade = ApplicationFacade(data_dir=data)
            facade.configure(settings, remember_key=True)
            facade.close()
            sentinel = data / "keep-user-data.txt"
            sentinel.write_text("synthetic user data", "utf-8")
            for stage in ("install", "reinstall"):
                run(
                    [
                        str(args.installer.resolve()),
                        "/VERYSILENT",
                        "/SUPPRESSMSGBOXES",
                        "/NORESTART",
                        "/TASKS=",
                        f"/DIR={install}",
                        f"/LOG={artifacts / ('m4-' + stage + '.log')}",
                    ]
                )
                assert registered() and (group / "MiniCodex.lnk").is_file()
                assert (group / "Uninstall.lnk").is_file()
                assert sentinel.read_text("utf-8") == "synthetic user data"
                assert ConfigurationStore(data).load() == settings
                scales = (1.0, 1.5, 2.0) if stage == "install" else (1.0,)
                for scale in scales:
                    environment["QT_SCALE_FACTOR"] = str(scale)
                    suffix = f"{stage}-{int(scale * 100)}"
                    report = artifacts / f"m4-{suffix}.json"
                    run(
                        [
                            str(install / "MiniCodex.exe"),
                            "--data-dir",
                            str(data),
                            "--smoke-test",
                            str(report),
                            "--screenshot",
                            str(artifacts / f"m4-{suffix}.png"),
                        ],
                        environment,
                    )
                    result = json.loads(report.read_text("utf-8"))
                    assert result["passed"] and result["frozen"]
                    assert result["settings_restored"] == {
                        "mode": "fake",
                        "model": "m4-offline-fixture",
                        "max_steps": 17,
                        "key_present": True,
                    }
                    assert abs(result["device_pixel_ratio"] - scale) < 0.01
                    results.append({"stage": suffix, **result})
                # On reinstall, a changed installed file must be replaced without touching data.
                if stage == "install":
                    (install / "README.md").write_text("verification old file", "utf-8")
                else:
                    assert (install / "README.md").read_text("utf-8") != "verification old file"
            run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
            assert not registered() and not (install / "MiniCodex.exe").exists()
            assert not (group / "MiniCodex.lnk").exists()
            assert sentinel.is_file() and (data / "sessions.sqlite3").is_file()
            assert ConfigurationStore(data).load() == settings
            for path in data.rglob("*"):
                if path.is_file():
                    assert secret.encode() not in path.read_bytes(), "Synthetic key leaked to disk"
        finally:
            try:
                if registered() and uninstaller.exists():
                    run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
                # Inno removes its own original executable asynchronously after returning.
                deadline = time.monotonic() + 10
                while uninstaller.exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
            finally:
                for target in config.credentials.targets(config.prefix):
                    config.credentials.delete(target)
    assert not registered() and not config.credentials.targets(config.prefix)
    report_path.write_text(
        json.dumps(
            {
                "passed": True,
                "isolated_verification_appid": True,
                "install_reinstall_uninstall": True,
                "shortcuts_verified": True,
                "user_data_and_vault_retained": True,
                "no_plaintext_key_in_data": True,
                "python_removed_from_path": True,
                "verification_cleanup_confirmed": True,
                "clean_windows_vm": False,
                "runs": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        "utf-8",
    )
    print("M4 install/reinstall/uninstall + persistence + 100/150/200% Qt DPI passed")


if __name__ == "__main__":
    main()
