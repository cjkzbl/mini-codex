import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def is_deepseek_url(base_url: str) -> bool:
    try:
        return urlsplit(base_url).hostname == "api.deepseek.com"
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class ModelSettings:
    mode: str = "fake"
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    api_key: str = field(default="", repr=False)
    streaming: bool = True
    timeout_s: float = 45.0
    max_steps: int = 20
    max_tool_calls: int = 50
    max_turn_s: float = 180.0
    execution_mode: str = "readonly"
    execution_environment: str = "native"
    command_timeout_s: float = 60.0
    docker_image: str = "python:3.12-slim"
    context_tokens: int = 32_000
    response_reserve_tokens: int = 4096
    max_turn_input_tokens: int = 512_000
    project_python: str = ""
    approval_policy: str = "ask"

    @property
    def permission_level(self) -> str:
        if self.execution_mode == "readonly":
            return "readonly"
        return "full" if self.approval_policy == "never" else "default"

    @property
    def is_deepseek(self) -> bool:
        return self.mode == "compatible" and is_deepseek_url(self.base_url)

    def __post_init__(self) -> None:
        if self.execution_environment not in {"native", "docker"}:
            raise ValueError("未知执行环境")
        # Active legacy modes identify their environment; readonly keeps the saved preference.
        if self.execution_mode in {"native", "docker"}:
            object.__setattr__(self, "execution_environment", self.execution_mode)
        if self.approval_policy not in {"ask", "never"}:
            raise ValueError("未知审批策略")
        if any(char in self.api_key for char in "\r\n\x00"):
            raise ValueError("密钥不能包含换行或空字符")
        if re.fullmatch(r"sk-[A-Za-z0-9_-]{16,}", self.model) or (
            self.api_key
            and any(
                self.api_key in value
                for value in (self.model, self.base_url, self.project_python, self.docker_image)
            )
        ):
            raise ValueError("请将 API Key 填在密钥栏，不能填入模型名称、地址或工具路径")
        if any(char in self.project_python for char in "\r\n\x00"):
            raise ValueError("项目 Python 路径不能包含换行或空字符")
        if not 8192 <= self.context_tokens <= 262_144:
            raise ValueError("上下文预算必须在 8192～262144 估算 Token")
        if not 512 <= self.response_reserve_tokens <= self.context_tokens // 2:
            raise ValueError("回答预留需在 512 Token 至上下文预算的一半之间")
        if not self.context_tokens <= self.max_turn_input_tokens <= 8_000_000:
            raise ValueError("每轮累计输入预算必须不低于上下文预算，且不超过 800 万")
        if self.mode not in {"fake", "compatible"}:
            raise ValueError("未知模型模式")
        if self.execution_mode not in {"readonly", "write", "native", "docker"}:
            raise ValueError("未知执行模式")
        if not 1 <= self.command_timeout_s <= 180:
            raise ValueError("命令超时必须在 1～180 秒")
        if (
            not self.docker_image
            or len(self.docker_image) > 256
            or any(c.isspace() for c in self.docker_image)
        ):
            raise ValueError("Docker 镜像名称格式无效")
        if not 1 <= self.max_steps <= 50 or not 1 <= self.max_tool_calls <= 100:
            raise ValueError("步骤/工具预算超出范围")
        if not 1 <= self.timeout_s <= 180 or not 1 <= self.max_turn_s <= 600:
            raise ValueError("超时必须在允许范围内")
        if self.mode == "compatible":
            url = urlsplit(self.base_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("API 地址必须是无认证信息、查询参数和 fragment 的 HTTP(S) URL")
            if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("远程模型地址必须使用 HTTPS；HTTP 仅允许本机服务")
            if not self.model.strip():
                raise ValueError("请填写服务支持的模型名称")
            if any(char in self.api_key for char in "\r\n"):
                raise ValueError("密钥不能包含换行")
