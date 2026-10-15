import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from minicodex.backend.application.dependencies import discover
from minicodex.backend.application.settings import ModelSettings, is_deepseek_url
from minicodex.backend.persistence.configuration import ConfigurationStore
from minicodex.frontend.ui.layout import fit_window


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: ModelSettings,
        parent: QWidget | None = None,
        *,
        configuration: ConfigurationStore | None = None,
        workspace: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("模型设置")
        self.setMinimumWidth(520)
        self.result_settings: ModelSettings | None = None
        self.configuration = configuration
        self._execution_mode = settings.execution_mode
        self._initial_environment = settings.execution_environment
        self._approval_policy = settings.approval_policy
        self.workspace = workspace
        self._initial_key = settings.api_key
        self._initial_host = urlsplit(settings.base_url).netloc.casefold()
        self.resize(620, 720)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll)
        layout = QVBoxLayout(body)
        note = QLabel(
            "远程模式会将对话和已读取的项目内容发送到所选服务。\n"
            "普通设置自动保存；勾选后将 Key 存入当前用户的 Windows 凭据管理器。"
            "权限在输入框左下角选择；修改权限和预算沿用当前对话，更换模型服务时新建对话。"
        )
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(note)
        self.deepseek_preset = QPushButton("填入 DeepSeek 官方配置")
        self.deepseek_preset.clicked.connect(self.apply_deepseek_preset)
        layout.addWidget(self.deepseek_preset)
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem("离线演示（真实列文件工具，无 API 费用）", "fake")
        self.mode.addItem("OpenAI-compatible API", "compatible")
        self.mode.setCurrentIndex(0 if settings.mode == "fake" else 1)
        self.execution_environment = QComboBox()
        self.execution_environment.setObjectName("executionEnvironmentPicker")
        self.execution_environment.addItem("Windows 原生执行", "native")
        self.execution_environment.addItem("Docker 沙箱", "docker")
        self.execution_environment.setCurrentIndex(
            self.execution_environment.findData(settings.execution_environment)
        )
        self.base_url = QLineEdit(settings.base_url)
        self.model = QLineEdit(settings.model)
        self.model.setPlaceholderText("服务支持的模型 ID，需支持工具调用")
        self.model_hint = QLabel(
            "模型名称是服务的模型 ID，例如 DeepSeek 的 deepseek-flash；不是 API Key。"
        )
        self.model_hint.setWordWrap(True)
        self.model_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.key = QLineEdit(settings.api_key)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("本机免认证服务可留空")
        self.remember_key = QCheckBox("将 API Key 保存到 Windows 凭据管理器")
        self.remember_key.setEnabled(configuration is not None and os.name == "nt")
        self.remember_key.setChecked(bool(configuration and configuration.remember_key))
        self.streaming = QCheckBox("启用流式响应")
        self.streaming.setChecked(settings.streaming)
        self.steps = QSpinBox()
        self.steps.setRange(1, 50)
        self.steps.setValue(settings.max_steps)
        self.tools = QSpinBox()
        self.tools.setRange(1, 100)
        self.tools.setValue(settings.max_tool_calls)
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(1, 180)
        self.timeout.setSuffix(" 秒")
        self.timeout.setValue(settings.timeout_s)
        self.turn_timeout = QDoubleSpinBox()
        self.turn_timeout.setRange(1, 600)
        self.turn_timeout.setSuffix(" 秒")
        self.turn_timeout.setValue(settings.max_turn_s)
        self.command_timeout = QDoubleSpinBox()
        self.command_timeout.setRange(1, 180)
        self.command_timeout.setSuffix(" 秒")
        self.command_timeout.setValue(settings.command_timeout_s)
        self.docker_image = QLineEdit(settings.docker_image)
        self.context_tokens = QSpinBox()
        self.context_tokens.setRange(8192, 262_144)
        self.context_tokens.setValue(settings.context_tokens)
        self.response_reserve = QSpinBox()
        self.response_reserve.setRange(512, 131_072)
        self.response_reserve.setValue(settings.response_reserve_tokens)
        self.turn_tokens = QSpinBox()
        self.turn_tokens.setRange(8192, 8_000_000)
        self.turn_tokens.setValue(settings.max_turn_input_tokens)
        self.project_python = QLineEdit(settings.project_python)
        self.project_python.setPlaceholderText("自动查找项目 .venv 或 PATH；也可选择 python.exe")
        python_row = QWidget()
        python_layout = QHBoxLayout(python_row)
        python_layout.setContentsMargins(0, 0, 0, 0)
        python_layout.addWidget(self.project_python, 1)
        browse = QPushButton("选择…")
        browse.clicked.connect(self.choose_python)
        python_layout.addWidget(browse)
        for title, widget in (
            ("模式", self.mode),
            ("执行环境", self.execution_environment),
            ("Docker 本地镜像", self.docker_image),
            ("API Base URL", self.base_url),
            ("模型名称", self.model),
            ("API Key", self.key),
            ("保存密钥", self.remember_key),
            ("输出方式", self.streaming),
            ("最大模型步骤", self.steps),
            ("最大工具调用", self.tools),
            ("单次 HTTP 超时", self.timeout),
            ("本轮总时限", self.turn_timeout),
            ("命令超时上限", self.command_timeout),
            ("上下文预算（估算 Token）", self.context_tokens),
            ("回答预留 Token", self.response_reserve),
            ("本轮累计输入预算", self.turn_tokens),
            ("目标项目 Python", python_row),
        ):
            form.addRow(title, widget)
        layout.addLayout(form)
        layout.addWidget(self.model_hint)
        self.deepseek_hint = QLabel("DeepSeek 思考模式已关闭，当前使用非思考模式进行工具调用。")
        self.deepseek_hint.setWordWrap(True)
        self.deepseek_hint.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.deepseek_hint)
        self.execution_hint = QLabel(
            "执行环境在上方选择，权限在输入框左下角选择："
            "只读 / 默认权限（逐次审批）/ 完全访问（自动执行）。"
            "更换执行环境不会改变审批权限；只读时不会执行命令。"
            "原生执行无 OS 沙箱，仅用于可信项目；"
            "Docker 命令在过滤后的临时项目副本运行，命令改动不写回；"
            "文件补丁仍按权限修改原项目。\n"
            + "检测到：Git "
            + (shutil.which("git") or "未安装")
            + "；Docker "
            + (shutil.which("docker") or "未安装")
            + "。Docker 还需 Linux 后端和预先准备的本地镜像。"
        )
        self.execution_hint.setWordWrap(True)
        self.execution_hint.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.execution_hint)
        self.dependency_report = QLabel()
        self.dependency_report.setWordWrap(True)
        self.dependency_report.setTextFormat(Qt.TextFormat.PlainText)
        dependency_button = QPushButton("重新检测工具路径（不执行项目代码）")
        dependency_button.clicked.connect(self.check_dependencies)
        layout.addWidget(dependency_button)
        layout.addWidget(self.dependency_report)
        maintenance = QHBoxLayout()
        forget = QPushButton("忘记保存的 Key")
        forget.setEnabled(configuration is not None)
        forget.clicked.connect(self.forget_key)
        maintenance.addWidget(forget)
        reset = QPushButton("重置配置…")
        reset.setEnabled(configuration is not None)
        reset.clicked.connect(self.reset_configuration)
        maintenance.addWidget(reset)
        layout.addLayout(maintenance)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.base_url.textChanged.connect(self.update_mode)
        self.execution_environment.currentIndexChanged.connect(self.update_execution_environment)
        self.update_mode()
        self.update_execution_environment()
        self.check_dependencies()
        screen = self.screen()
        if screen:
            self.resize(fit_window(screen.availableGeometry().size(), self.size()))

    def choose_python(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择目标项目 Python", "", "Python (python.exe)"
        )
        if path:
            self.project_python.setText(path)
            self.check_dependencies()

    def check_dependencies(self) -> None:
        from dataclasses import replace

        try:
            settings = replace(ModelSettings(), project_python=self.project_python.text().strip())
        except ValueError as error:
            self.dependency_report.setText(str(error))
            return
        self.dependency_report.setText(
            "\n\n".join(
                f"{item.name}：{item.path or '未找到'}\n{item.hint}"
                for item in discover(settings, self.workspace)
            )
        )

    def forget_key(self) -> None:
        if self.configuration:
            try:
                self.configuration.forget()
                self.remember_key.setChecked(False)
                self.error_label.setText(
                    "Windows 中保存的 Key 已删除；当前进程内存中的 Key 在退出后清空。"
                )
            except (OSError, ValueError) as error:
                self.error_label.setText(str(error))

    def reset_configuration(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if (
            self.configuration
            and QMessageBox.question(
                self,
                "重置配置",
                "重置模型、预算、执行模式并删除保存的 Key？会话记录会保留。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            try:
                self.configuration.reset()
                self.result_settings = ModelSettings()
                self.remember_key.setChecked(False)
                self.accept()
            except (OSError, ValueError) as error:
                self.error_label.setText(str(error))

    def apply_deepseek_preset(self) -> None:
        self.mode.setCurrentIndex(1)
        self.base_url.setText("https://api.deepseek.com")
        self.model.setText("deepseek-flash")
        self.error_label.clear()

    def update_execution_environment(self) -> None:
        self.docker_image.setEnabled(self.execution_environment.currentData() == "docker")

    def update_mode(self) -> None:
        try:
            host = urlsplit(self.base_url.text().strip()).netloc.casefold()
        except ValueError:
            host = ""
        if (
            host
            and host != self._initial_host
            and self._initial_key
            and self.key.text() == self._initial_key
        ):
            self.key.clear()
            self.key.setPlaceholderText("服务地址已变更，请输入对应服务的 Key")
        enabled = self.mode.currentData() == "compatible"
        for widget in (self.base_url, self.model, self.key, self.streaming, self.timeout):
            widget.setEnabled(enabled)
        self.deepseek_hint.setVisible(enabled and is_deepseek_url(self.base_url.text().strip()))

    def save(self) -> None:
        try:
            environment = self.execution_environment.currentData()
            execution_mode = self._execution_mode
            if execution_mode in {"native", "docker"} or (
                execution_mode == "write" and environment != self._initial_environment
            ):
                execution_mode = environment
            self.result_settings = ModelSettings(
                mode=self.mode.currentData(),
                base_url=self.base_url.text().strip(),
                model=self.model.text().strip(),
                api_key=self.key.text().strip(),
                streaming=self.streaming.isChecked(),
                max_steps=self.steps.value(),
                max_tool_calls=self.tools.value(),
                timeout_s=self.timeout.value(),
                max_turn_s=self.turn_timeout.value(),
                execution_mode=execution_mode,
                execution_environment=environment,
                approval_policy=self._approval_policy,
                command_timeout_s=self.command_timeout.value(),
                docker_image=self.docker_image.text().strip(),
                context_tokens=self.context_tokens.value(),
                response_reserve_tokens=self.response_reserve.value(),
                max_turn_input_tokens=self.turn_tokens.value(),
                project_python=self.project_python.text().strip(),
            )
        except ValueError as error:
            self.error_label.setText(str(error))
            return
        self.accept()
