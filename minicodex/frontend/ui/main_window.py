import json
from dataclasses import replace
from pathlib import Path
from sqlite3 import Error as SQLiteError

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from minicodex import __version__
from minicodex.backend.application.commands import StartTurn
from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.core.events import TERMINAL_EVENTS, EventKind, RuntimeEvent
from minicodex.backend.core.models import ThreadState
from minicodex.frontend.ui.conversation import ConversationView
from minicodex.frontend.ui.event_bridge import EventBridge
from minicodex.frontend.ui.layout import fit_window
from minicodex.frontend.ui.prompt_edit import PromptEdit
from minicodex.frontend.ui.session_row import SessionRow
from minicodex.frontend.ui.settings_dialog import SettingsDialog
from minicodex.frontend.ui.theme import STYLESHEET


def label(text: str, name: str = "muted") -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def panel() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 20, 18, 20)
    layout.setSpacing(14)
    return frame, layout


class MainWindow(QMainWindow):
    def __init__(self, facade: ApplicationFacade | None = None) -> None:
        super().__init__()
        self.facade = facade or ApplicationFacade()
        self._turn_id: str | None = None
        self._sequence = 0
        self._approval_id: str | None = None
        self._workspace: Path | None = None
        self.setWindowTitle("MiniCodex · 编程工作台")
        self.resize(1280, 920)
        self.setMinimumSize(960, 640)
        screen = QApplication.primaryScreen()
        if screen and QApplication.platformName() != "offscreen":
            available = screen.availableGeometry().size()
            self.setMinimumSize(min(960, available.width() - 24), min(640, available.height() - 24))
            requested = (
                self.facade.configuration.record.get("window_size", [1280, 920])
                if self.facade.configuration
                else [1280, 920]
            )
            if (
                not isinstance(requested, list)
                or len(requested) != 2
                or not all(isinstance(v, int) and 320 <= v <= 10000 for v in requested)
            ):
                requested = [1280, 920]
            self.resize(fit_window(available, QSize(*requested)))
        self.setStyleSheet(STYLESHEET)
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 12, 12, 6)
        outer.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(label("MiniCodex", "brand"))
        self.subtitle = label("你的本地编程工作台")
        header.addWidget(self.subtitle)
        header.addStretch()
        self.settings_button = QPushButton("模型设置")
        self.settings_button.clicked.connect(self.configure_model)
        header.addWidget(self.settings_button)
        outer.addLayout(header)

        split = QSplitter(Qt.Orientation.Horizontal)
        left, left_layout = panel()
        left.setObjectName("sidebar")
        left.setMinimumWidth(200)
        left_layout.addWidget(label("工作区", "section"))
        self.open_button = QPushButton("打开项目文件夹")
        self.open_button.clicked.connect(self.choose_workspace)
        left_layout.addWidget(self.open_button)
        self.workspace_label = label("选择一个本地代码目录，开始体验桌面任务流程。")
        self.workspace_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        left_layout.addWidget(self.workspace_label)
        self.workspace_path = QPlainTextEdit()
        self.workspace_path.setReadOnly(True)
        self.workspace_path.setMaximumHeight(70)
        self.workspace_path.setPlaceholderText("项目路径")
        self.workspace_path.hide()
        left_layout.addWidget(self.workspace_path)
        self.recent_projects = QComboBox()
        self.recent_projects.setMinimumWidth(0)
        self.recent_projects.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.recent_projects.setMinimumContentsLength(10)
        self.recent_projects.activated.connect(self.open_recent_project)
        left_layout.addWidget(self.recent_projects)
        left_layout.addSpacing(12)
        left_layout.addWidget(label("对话", "section"))
        self.sessions = QListWidget()
        self.sessions.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sessions.itemClicked.connect(self.resume_session)
        self.sessions.itemActivated.connect(self.resume_session)
        left_layout.addWidget(self.sessions, 1)
        self.new_session_button = QPushButton("＋ 新建对话")
        self.new_session_button.setEnabled(False)
        self.new_session_button.clicked.connect(self.new_session)
        left_layout.addWidget(self.new_session_button)
        self.storage_label = label(
            "会话和配置自动保存在本机\n密钥可选择存入 Windows 凭据。"
            if self.facade.store
            else "当前为临时会话模式\n关闭程序后不会保存。"
        )
        if self.facade.store:
            self.storage_label.setToolTip(str(self.facade.store.directory))
        left_layout.addWidget(self.storage_label)

        center, center_layout = panel()
        title = QHBoxLayout()
        self.conversation_title = label("新对话", "section")
        title.addWidget(self.conversation_title, 1)
        title.addStretch()
        self.status_label = label("等待选择项目")
        title.addWidget(self.status_label)
        center_layout.addLayout(title)
        self.chat = ConversationView()
        self.chat.load(None)
        center_layout.addWidget(self.chat, 1)
        composer = QFrame()
        composer.setObjectName("composer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(8, 6, 8, 8)
        composer_layout.setSpacing(4)
        self.prompt = PromptEdit()
        self.prompt.setObjectName("prompt")
        self.prompt.setPlaceholderText("向 MiniCodex 描述任务…\nEnter 发送 · Shift+Enter 换行")
        self.prompt.setMaximumHeight(105)
        self.prompt.send_requested.connect(self.send_turn)
        composer_layout.addWidget(self.prompt)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.permission_picker = QComboBox()
        self.permission_picker.setObjectName("permissionPicker")
        for permission_title, level, hint in (
            ("只读", "readonly", "查看项目；不修改文件、不运行命令。"),
            (
                "默认权限",
                "default",
                "允许修改和 Windows 原生执行，每次操作审批；原生执行无 OS 沙箱。",
            ),
            (
                "完全访问",
                "full",
                "自动修改和运行命令，不逐次审批；原生进程具有当前用户权限。"
                "仍保留工具的路径、输出和预算限制。",
            ),
        ):
            self.permission_picker.addItem(permission_title, level)
            self.permission_picker.setItemData(
                self.permission_picker.count() - 1, hint, Qt.ItemDataRole.ToolTipRole
            )
        self.permission_picker.activated.connect(self.change_permissions)
        actions.addWidget(self.permission_picker)
        actions.addStretch()
        self.model_button = QPushButton()
        self.model_button.setObjectName("modelPicker")
        self.model_button.clicked.connect(self.configure_model)
        actions.addWidget(self.model_button)
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_turn)
        self.send_button = QPushButton("↑")
        self.send_button.setAccessibleName("发送任务")
        self.send_button.setToolTip("发送任务（Enter）")
        self.send_button.setObjectName("primary")
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self.send_turn)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.send_button)
        composer_layout.addLayout(actions)
        center_layout.addWidget(composer)
        self.approval_panel = QFrame()
        self.approval_panel.setObjectName("approvalCard")
        approval_layout = QVBoxLayout(self.approval_panel)
        approval_layout.setContentsMargins(12, 12, 12, 12)
        self.approval_title = label("等待操作审批", "section")
        approval_layout.addWidget(self.approval_title)
        self.approval_preview = QPlainTextEdit()
        self.approval_preview.setReadOnly(True)
        self.approval_preview.setMaximumHeight(170)
        approval_layout.addWidget(self.approval_preview)
        approval_buttons = QHBoxLayout()
        self.approve_button = QPushButton("批准本次操作")
        self.approve_button.setObjectName("approve")
        self.reject_button = QPushButton("拒绝")
        self.approve_button.clicked.connect(lambda: self.answer_approval(True))
        self.reject_button.clicked.connect(lambda: self.answer_approval(False))
        approval_buttons.addWidget(self.approve_button)
        approval_buttons.addWidget(self.reject_button)
        approval_layout.addLayout(approval_buttons)
        self.approval_panel.hide()
        center_layout.insertWidget(2, self.approval_panel)
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        sidebar_scroll.setWidget(left)
        sidebar_scroll.setMinimumWidth(220)
        split.addWidget(sidebar_scroll)
        split.addWidget(center)
        split.setSizes([230, 1050])
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)
        outer.addWidget(split, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage(f"编程助手  /  Windows-first  /  v{__version__}")
        self.bridge = EventBridge(self.facade, self)
        self.bridge.event_received.connect(self.on_event)
        self.rename_shortcut = QShortcut(QKeySequence("F2"), self.sessions)
        self.rename_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.rename_shortcut.activated.connect(lambda: self.rename_session())
        self.extra_shortcuts: list[QShortcut] = []
        for key, callback in (
            ("Ctrl+O", self.choose_workspace),
            ("Ctrl+N", self.new_session),
            ("Ctrl+,", self.configure_model),
            ("Ctrl+Shift+E", self.export_trace),
            ("Ctrl+L", self.prompt.setFocus),
            ("Escape", self.stop_turn),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
            self.extra_shortcuts.append(shortcut)
        self.refresh_recent_projects()
        self.refresh_mode()
        self.refresh_sessions()
        for saved in self.facade.thread_manager.list_threads():
            if saved.workspace.is_dir():
                self.load_thread(self.facade.resume(saved.thread_id))
                break
        if self.facade.configuration and self.facade.configuration.warning:
            self.statusBar().showMessage(self.facade.configuration.warning)

    def refresh_recent_projects(self) -> None:
        self.recent_projects.clear()
        self.recent_projects.addItem("最近项目…", None)
        config = self.facade.configuration
        projects = config.record.get("recent_projects", []) if config else []
        if not isinstance(projects, list):
            projects = []
        for path in projects[:10]:
            if isinstance(path, str):
                self.recent_projects.addItem(Path(path).name, path)
                self.recent_projects.setItemData(
                    self.recent_projects.count() - 1, path, Qt.ItemDataRole.ToolTipRole
                )

    def open_recent_project(self, index: int) -> None:
        path = self.recent_projects.itemData(index)
        if path and not self.facade.is_running:
            self.open_workspace(path)

    def export_trace(self) -> None:
        thread = self.facade.thread
        if not thread or self.facade.is_running:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出脱敏任务记录", "minicodex-trace.jsonl", "JSONL (*.jsonl)"
        )
        if path:
            try:
                self.facade.export_trace(thread.thread_id, Path(path))
                self.statusBar().showMessage("已导出脱敏任务记录：" + path)
            except (OSError, RuntimeError, ValueError) as error:
                QMessageBox.warning(self, "无法导出", str(error))

    def refresh_mode(self) -> None:
        remote = self.facade.settings.mode == "compatible"
        self.permission_picker.setCurrentIndex(
            self.permission_picker.findData(self.facade.settings.permission_level)
        )
        self.permission_picker.setToolTip(
            self.permission_picker.currentData(Qt.ItemDataRole.ToolTipRole)
        )
        model = self.facade.settings.model if remote else "离线演示"
        self.model_button.setText(
            self.model_button.fontMetrics().elidedText(model, Qt.TextElideMode.ElideRight, 200)
        )
        self.model_button.setToolTip(
            model + (" · API 模式，按服务计费" if remote else " · 无 API 费用")
        )

    def change_permissions(self, index: int) -> None:
        if self.facade.is_running:
            self.refresh_mode()
            return
        level = self.permission_picker.itemData(index)
        if level not in {"readonly", "default", "full"}:
            self.refresh_mode()
            return
        try:
            self.facade.configure(
                replace(
                    self.facade.settings,
                    execution_mode="readonly" if level == "readonly" else "native",
                    approval_policy="never" if level == "full" else "ask",
                )
            )
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "权限未应用", str(error))
        self.refresh_mode()
        self.prompt.setFocus()

    def choose_workspace(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择本地项目文件夹")
        if path:
            self.open_workspace(path)

    def configure_model(self) -> None:
        if self.facade.is_running:
            return
        dialog = SettingsDialog(
            self.facade.settings,
            self,
            configuration=self.facade.configuration,
            workspace=self.facade.thread.workspace if self.facade.thread else None,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_settings:
            try:
                self.facade.configure(
                    dialog.result_settings, remember_key=dialog.remember_key.isChecked()
                )
            except (OSError, ValueError, RuntimeError) as error:
                QMessageBox.warning(self, "设置未应用", str(error))
                dialog.deleteLater()
                return
            self.refresh_mode()
            if self.facade.thread:
                self.load_thread(self.facade.thread)
        dialog.deleteLater()

    def new_session(self) -> None:
        workspace = self.facade.thread.workspace if self.facade.thread else self._workspace
        if workspace and not self.facade.is_running:
            self.open_workspace(workspace)

    def rename_session(self, item: QListWidgetItem | None = None) -> None:
        if self.facade.is_running:
            return
        item = item or self.sessions.currentItem()
        if item is None:
            return
        thread_id = item.data(Qt.ItemDataRole.UserRole)
        self.rename_thread(thread_id)

    def rename_thread(self, thread_id: str) -> None:
        if self.facade.is_running:
            return
        try:
            saved = self.facade.thread_manager.store.get(thread_id)
        except ValueError as error:
            QMessageBox.warning(self, "无法重命名", str(error))
            return
        title, accepted = QInputDialog.getText(
            self, "重命名对话", "对话名称（最多 80 个字符）", text=saved.title
        )
        if accepted:
            try:
                self.facade.rename_thread(thread_id, title)
            except (OSError, ValueError, RuntimeError, SQLiteError) as error:
                QMessageBox.warning(self, "无法重命名", str(error))
                return
            self.refresh_sessions()

    def delete_session(self, thread_id: str) -> None:
        if self.facade.is_running:
            return
        try:
            saved = self.facade.thread_manager.store.get(thread_id)
        except ValueError as error:
            QMessageBox.warning(self, "无法删除对话", str(error))
            return
        if (
            QMessageBox.question(
                self,
                "删除对话",
                f"删除对话“{saved.title}”及其消息记录？\n项目文件不会被删除。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        current = self.facade.thread
        try:
            remaining = self.facade.delete_thread(thread_id)
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "无法删除对话", str(error))
            return
        if current and current.thread_id == thread_id:
            self.prompt.clear()
            if remaining:
                self.load_thread(remaining)
            else:
                self.chat.load(None)
                self.conversation_title.setText("新对话")
                self.status_label.setText("对话已删除，可新建对话")
                self._turn_id = None
                self.clear_approval()
                self.send_button.setEnabled(False)
                self.new_session_button.setEnabled(self._workspace is not None)
        self.refresh_sessions()

    def select_session(self, thread_id: str) -> None:
        for index in range(self.sessions.count()):
            item = self.sessions.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == thread_id:
                self.sessions.setCurrentItem(item)
                self.resume_session(item)
                return

    def refresh_sessions(self) -> None:
        self.sessions.clear()
        thread = self.facade.thread
        for saved in self.facade.thread_manager.list_threads():
            item = QListWidgetItem()
            item.setToolTip(saved.title + "\n项目：" + str(saved.workspace))
            item.setData(Qt.ItemDataRole.UserRole, saved.thread_id)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, saved.title)
            item.setSizeHint(QSize(0, 42))
            self.sessions.addItem(item)
            row = SessionRow(saved.title)
            row.activated.connect(lambda identity=saved.thread_id: self.select_session(identity))
            row.rename_requested.connect(
                lambda identity=saved.thread_id: self.rename_thread(identity)
            )
            row.delete_requested.connect(
                lambda identity=saved.thread_id: self.delete_session(identity)
            )
            self.sessions.setItemWidget(item, row)
            if thread and saved.thread_id == thread.thread_id:
                self.sessions.setCurrentItem(item)
                self.conversation_title.setText(saved.title)

    def resume_session(self, item: QListWidgetItem) -> None:
        if (
            self.facade.thread
            and item.data(Qt.ItemDataRole.UserRole) == self.facade.thread.thread_id
        ):
            return
        try:
            thread = self.facade.resume(item.data(Qt.ItemDataRole.UserRole))
        except (ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "无法恢复会话", str(error))
            return
        self.load_thread(thread)

    def open_workspace(self, path: str | Path) -> None:
        try:
            thread = self.facade.select_workspace(path)
        except (ValueError, RuntimeError, OSError) as error:
            QMessageBox.warning(self, "无法打开项目", str(error))
            return
        self.load_thread(thread)

    def load_thread(self, thread: ThreadState) -> None:
        self._workspace = thread.workspace
        self.refresh_mode()
        self.refresh_recent_projects()
        self.workspace_label.setText(thread.workspace.name)
        self.workspace_label.setToolTip(str(thread.workspace))
        self.workspace_path.setPlainText(str(thread.workspace))
        self.workspace_path.show()
        self.refresh_sessions()
        self.chat.load(thread)
        self.clear_approval()
        self.new_session_button.setEnabled(True)
        self._turn_id = None
        self.send_button.setEnabled(True)
        self.status_label.setText("就绪")
        self.prompt.setFocus()

    def send_turn(self) -> None:
        text = self.prompt.toPlainText().strip()
        thread = self.facade.thread
        if not text or thread is None or self.facade.is_running:
            return
        try:
            turn = self.facade.start(StartTurn(thread.thread_id, text))
        except (ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "无法开始任务", str(error))
            return
        self._turn_id, self._sequence = turn.turn_id, 0
        self.chat.add_turn(turn)
        self.refresh_sessions()
        self.prompt.clear()
        self._set_running(True)
        self.status_label.setText("运行中")

    def _set_running(self, running: bool) -> None:
        self.open_button.setEnabled(not running)
        self.sessions.setEnabled(not running)
        self.recent_projects.setEnabled(not running)
        self.settings_button.setEnabled(not running)
        self.model_button.setEnabled(not running)
        self.permission_picker.setEnabled(not running)
        self.new_session_button.setEnabled(not running and self.facade.thread is not None)
        self.send_button.setEnabled(not running and self.facade.thread is not None)
        self.stop_button.setEnabled(running)

    def stop_turn(self) -> None:
        if self.facade.is_running:
            self.chat.stop_thinking()
            self.clear_approval()
            self.facade.cancel()
            self.stop_button.setEnabled(False)
            self.status_label.setText("正在停止…")

    def clear_approval(self) -> None:
        self._approval_id = None
        self.approval_panel.hide()
        self.approve_button.setEnabled(False)
        self.reject_button.setEnabled(False)

    def answer_approval(self, approved: bool) -> None:
        if self._approval_id and self.facade.is_running:
            self.facade.respond_approval(self._approval_id, approved)
            self.approve_button.setEnabled(False)
            self.reject_button.setEnabled(False)
            self.approval_title.setText("已提交批准" if approved else "已提交拒绝")

    def on_event(self, event: RuntimeEvent) -> None:
        thread = self.facade.thread
        if (
            thread is None
            or event.thread_id != thread.thread_id
            or event.turn_id != self._turn_id
            or event.sequence <= self._sequence
        ):
            return
        self._sequence = event.sequence
        if self.facade.store and self.facade.store.audit_warning:
            self.statusBar().showMessage(self.facade.store.audit_warning)
        self.chat.consume(event)
        if event.kind == EventKind.APPROVAL_REQUESTED:
            request = json.loads(event.text)
            self._approval_id = request["approval_id"]
            self.approval_title.setText("审批：" + request["tool"] + " · " + request["mode"])
            self.approval_preview.setPlainText(request["summary"])
            self.approval_panel.show()
            self.approve_button.setEnabled(True)
            self.reject_button.setEnabled(True)
            self.status_label.setText("等待审批")
        elif event.kind == EventKind.APPROVAL_RESOLVED:
            request = json.loads(event.text)
            if request["approval_id"] == self._approval_id:
                self.clear_approval()
                self.status_label.setText("运行中")
        elif event.kind == EventKind.MODEL_DELTA:
            self.status_label.setText("正在输出")
        elif event.kind == EventKind.STEP_STARTED:
            self.status_label.setText(event.text.split("\n", 1)[0])
        elif event.kind == EventKind.TOOL_STARTED:
            self.status_label.setText("调用工具 · " + event.text.split("\n", 1)[0])
        if event.kind in TERMINAL_EVENTS:
            self.clear_approval()
            self._set_running(False)
            titles = {
                EventKind.TURN_COMPLETED: "已完成",
                EventKind.TURN_CANCELLED: "已停止",
                EventKind.TURN_FAILED: "失败",
                EventKind.TURN_LIMIT_EXCEEDED: "达到上限",
                EventKind.TURN_INTERRUPTED: "结果未知",
            }
            self.status_label.setText(titles[event.kind])

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "subtitle"):
            self.subtitle.setVisible(self.width() >= 1150)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.facade.is_running:
            choice = QMessageBox.question(
                self,
                "关闭 MiniCodex",
                "任务正在运行。停止任务并关闭窗口？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.chat.stop_thinking()
        if not self.facade.close():
            QMessageBox.warning(self, "正在停止", "后台任务尚未退出，请稍后重试。")
            event.ignore()
            return
        if self.facade.configuration:
            try:
                self.facade.configuration.save_window(self.width(), self.height())
            except (OSError, ValueError):
                pass  # Conversation is already safe; a geometry failure must not prevent shutdown.
        self.bridge.timer.stop()
        event.accept()
