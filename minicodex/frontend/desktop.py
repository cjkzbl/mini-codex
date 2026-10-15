import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QCoreApplication, QLockFile, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from minicodex import __version__
from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.models import ModelResponse, ToolCall
from minicodex.backend.llm.fake import FakeLLMProvider
from minicodex.backend.persistence.paths import default_data_directory
from minicodex.backend.persistence.sqlite_store import SQLiteThreadStore
from minicodex.frontend.ui.fonts import configure_fonts
from minicodex.frontend.ui.main_window import MainWindow
from minicodex.frontend.windows_client import (
    APP_MUTEX,
    MAINTENANCE_MUTEX,
    NamedMutex,
    purge_default_data,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="MiniCodex Windows 桌面原型")
    parser.add_argument("--smoke-test", type=Path, help="离线自检，将结果写入 JSON 后退出")
    parser.add_argument("--screenshot", type=Path, help="自检完成后保存界面 PNG")
    parser.add_argument("--smoke-api", help="自检专用：本机兼容 API 的 Base URL")
    parser.add_argument(
        "--smoke-m2", action="store_true", help="临时项目上的审批/修改/原生测试自检"
    )
    parser.add_argument("--smoke-python", help="M2 自检使用的目标项目 Python 可执行程序")
    parser.add_argument("--data-dir", type=Path, help="本机会话数据库与归档目录")
    parser.add_argument(
        "--purge-user-data", action="store_true", help="将默认用户数据移至回收站并删除保存的凭据"
    )
    parser.add_argument("--confirm-purge", action="store_true", help="确认上述数据清理")
    args = parser.parse_args()
    if args.purge_user_data:
        if not args.confirm_purge or args.data_dir or args.smoke_test:
            parser.error("清理仅支持默认数据目录，需 --confirm-purge，不能与自检混用")
        core = QCoreApplication(sys.argv[:1])
        try:
            purge_default_data()
            return 0
        except (OSError, ValueError, RuntimeError):
            return 1
        finally:
            del core
    if args.smoke_m2 and (not args.smoke_test or not args.smoke_python or args.smoke_api):
        parser.error("--smoke-m2 需 --smoke-test、--smoke-python，不能与 --smoke-api 混用")
    if args.smoke_api and (
        not args.smoke_test
        or urlsplit(args.smoke_api).hostname not in {"127.0.0.1", "localhost", "::1"}
    ):
        parser.error("--smoke-api 只能配合 --smoke-test 使用本机地址")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv[:1])
    app.setApplicationName("MiniCodex")
    app.setOrganizationName("MiniCodex")
    configure_fonts(app)
    presence = NamedMutex(APP_MUTEX)
    if presence.exists(MAINTENANCE_MUTEX):
        presence.close()
        QMessageBox.warning(None, "正在维护数据", "数据清理正在进行，请稍后启动。")
        return 1
    smoke_data = (
        tempfile.TemporaryDirectory(prefix="MiniCodex selftest data ") if args.smoke_test else None
    )
    data_dir = args.data_dir or (Path(smoke_data.name) if smoke_data else default_data_directory())
    data_dir.mkdir(parents=True, exist_ok=True)
    data_lock = QLockFile(str(data_dir / "desktop.lock"))
    data_lock.setStaleLockTime(0)
    if not data_lock.tryLock(0):
        QMessageBox.warning(None, "会话目录正在使用", "另一个 MiniCodex 正在使用此数据目录。")
        presence.close()
        return 1
    try:
        facade = ApplicationFacade(data_dir=data_dir)
    except Exception as error:
        QMessageBox.critical(None, "无法加载会话", str(error) + "\n原数据已保留，请检查存储目录。")
        data_lock.unlock()
        presence.close()
        return 1
    if args.smoke_api:
        facade.configure(
            ModelSettings(
                mode="compatible",
                base_url=args.smoke_api,
                model="local-smoke-fixture",
                streaming=False,
            )
        )
    window = MainWindow(facade)
    window.show()
    smoke_timer: QTimer | None = None
    temporary: tempfile.TemporaryDirectory[str] | None = None
    exit_code = 0
    if args.smoke_test:
        temporary = tempfile.TemporaryDirectory(prefix="MiniCodex 中文 项目 ")
        project = Path(temporary.name)
        (project / "README.md").write_text("# 中文项目\n工具自检\n", "utf-8")
        if args.smoke_m2:
            facade.configure(ModelSettings(execution_mode="native"))
            (project / "calc.py").write_bytes(b"def add(a, b):\r\n    return a-b\r\n")
            facade.runtime.provider = FakeLLMProvider(
                [
                    ModelResponse(tool_calls=(ToolCall("read", "read_file", {"path": "calc.py"}),)),
                    ModelResponse(
                        tool_calls=(
                            ToolCall(
                                "patch",
                                "apply_patch",
                                {
                                    "patch": "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
                                    " def add(a, b):\n-    return a-b\n+    return a+b\n"
                                },
                            ),
                        )
                    ),
                    ModelResponse(
                        tool_calls=(
                            ToolCall(
                                "test",
                                "run_command",
                                {
                                    "argv": [
                                        args.smoke_python,
                                        "-c",
                                        "from calc import add;assert add(2,3)==5;"
                                        "print('M2 test passed')",
                                    ]
                                },
                            ),
                        )
                    ),
                    ModelResponse(text="M2 patch/test verified."),
                ],
                delay_s=0.025,
            )
        window.open_workspace(temporary.name)
        window.prompt.setPlainText("验证桌面后台任务流程")
        window.send_turn()
        started = time.monotonic()
        ticks = 0
        approvals_submitted: set[str] = set()
        smoke_timer = QTimer(window)
        smoke_timer.setInterval(10)

        def check() -> None:
            nonlocal ticks, exit_code
            ticks += 1
            if (
                args.smoke_m2
                and window._approval_id
                and window._approval_id not in approvals_submitted
            ):
                approvals_submitted.add(window._approval_id)
                window.answer_approval(True)
            if window.facade.is_running and time.monotonic() - started < 20:
                return
            assert smoke_timer is not None
            smoke_timer.stop()
            thread = window.facade.thread
            complete = window.status_label.text() == "已完成"
            last_turn = thread.turns[-1] if thread and thread.turns else None
            card = window.chat.cards.get(last_turn.turn_id) if last_turn else None
            ui_verified = bool(
                last_turn
                and card
                and len(card.steps) >= 2
                and "用时" in card.summary.text()
                and "已编辑" in card.summary.text()
                and len(last_turn.edited_files) == (1 if args.smoke_m2 else 0)
            )
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(args.screenshot))
            stopped = window.facade.close()
            restored = SQLiteThreadStore(data_dir)
            restored_thread = restored.get(thread.thread_id) if thread else None
            persistence_verified = bool(
                thread
                and restored_thread
                and restored_thread.turns
                and restored_thread.turns[-1].status.value == "completed"
                and restored_thread.title == thread.title
            )
            restored.close()
            tool_verified = "README.md" in window.chat.toPlainText()
            if args.smoke_m2:
                tool_verified = (
                    (project / "calc.py").read_bytes() == b"def add(a, b):\r\n    return a+b\r\n"
                    and card is not None
                    and any(
                        "M2 test passed" in tool.log
                        for step_card in card.steps.values()
                        for tool in step_card.tools.values()
                    )
                    and len(approvals_submitted) == 2
                )
            report = {
                "passed": complete
                and stopped
                and ticks >= 3
                and tool_verified
                and ui_verified
                and persistence_verified,
                "persistence_verified": persistence_verified,
                "settings_restored": {
                    "mode": facade.settings.mode,
                    "model": facade.settings.model,
                    "max_steps": facade.settings.max_steps,
                    "key_present": bool(facade.settings.api_key),
                },
                "device_pixel_ratio": window.devicePixelRatioF(),
                "status": window.status_label.text(),
                "gui_timer_ticks": ticks,
                "worker_stopped": stopped,
                "history_messages": len(thread.history) if thread else 0,
                "provider": facade.settings.mode,
                "tool_verified": tool_verified,
                "ui_verified": ui_verified,
                "steps": len(card.steps) if card else 0,
                "duration_s": last_turn.duration_s if last_turn else 0,
                "edited_files": sorted(last_turn.edited_files) if last_turn else [],
                "m2": args.smoke_m2,
                "approvals_submitted": len(approvals_submitted),
                "version": __version__,
                "frozen": bool(getattr(sys, "frozen", False)),
                "python": sys.version.split()[0],
            }
            args.smoke_test.parent.mkdir(parents=True, exist_ok=True)
            args.smoke_test.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
            exit_code = 0 if report["passed"] else 1
            app.quit()

        smoke_timer.timeout.connect(check)
        smoke_timer.start()
    try:
        result = app.exec()
        return exit_code or result
    finally:
        window.facade.close()
        if temporary:
            temporary.cleanup()
        data_lock.unlock()
        presence.close()
        if smoke_data:
            smoke_data.cleanup()
