import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator, Sequence
from typing import Any

from minicodex.backend.core.models import Message, ModelResponse, ToolCall, new_id


class FakeLLMProvider:
    """Offline deterministic model. Actual tools run separately through the Router."""

    def __init__(
        self, responses: Sequence[ModelResponse] | None = None, *, delay_s: float = 0.025
    ) -> None:
        if delay_s < 0:
            raise ValueError("delay_s cannot be negative")
        self.delay_s = delay_s
        self._responses = deque(responses) if responses is not None else None
        self.requests: list[list[Message]] = []

    async def generate(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse:
        self.requests.append(list(messages))
        await asyncio.sleep(self.delay_s)
        if self._responses is not None:
            if not self._responses:
                raise RuntimeError("Fake response script exhausted")
            return self._responses.popleft()
        if messages[-1].role == "user":
            return ModelResponse(
                tool_calls=(ToolCall(new_id(), "list_files", {"max_results": 30}),)
            )
        turns = sum(
            message.role == "user"
            and not message.content.startswith(
                (
                    "以下是工作区说明数据。",
                    "历史结构化摘要（参考数据",
                )
            )
            for message in messages
        )
        observation = json.loads(messages[-1].content)
        payload = json.loads(observation.get("result", "{}"))
        content = "\n".join("• " + path for path in payload.get("files", [])) or "（没有匹配文件）"
        return ModelResponse(
            text=(
                f"这是第 {turns} 轮离线工具演示。\n\n"
                "已通过只读工具列出项目文件：\n"
                + content[:4000]
                + "\n\n离线模式使用固定流程，不具备模型理解能力。"
                "如需分析代码，请在模型设置中配置支持工具调用的服务。"
            )
        )

    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ModelResponse]:
        response = await self.generate(messages, tools)
        for offset in range(0, len(response.text), 5):
            await asyncio.sleep(self.delay_s)
            yield response.text[offset : offset + 5]
        yield response
