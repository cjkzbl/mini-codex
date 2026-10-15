import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.models import Message, ModelResponse, ToolCall
from minicodex.backend.llm.http_errors import explain_http_error


class ProviderError(RuntimeError):
    """Sanitized failure: no response bodies or auth headers."""


def wire_messages(messages: list[Message]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        if message.tool_call_id:
            item["tool_call_id"] = message.tool_call_id
        items.append(item)
    return items


def parse_calls(items: list[dict[str, Any]]) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    seen: set[str] = set()
    for item in items:
        function = item.get("function", {})
        call_id, name = item.get("id"), function.get("name")
        if not isinstance(call_id, str) or not isinstance(name, str) or not call_id or not name:
            raise ProviderError("工具调用缺少完整 id/name")
        if call_id in seen:
            raise ProviderError("模型返回重复的工具 call_id")
        seen.add(call_id)
        try:
            raw = function.get("arguments", "")
            arguments = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            raise ProviderError("工具参数不是有效 JSON") from None
        if not isinstance(arguments, dict):
            raise ProviderError("工具参数必须是 JSON object")
        calls.append(ToolCall(call_id, name, arguments))
    if len(calls) > 16:
        raise ProviderError("单次模型请求超过工具调用上限")
    return tuple(calls)


def parse_response(data: dict[str, Any]) -> ModelResponse:
    try:
        choice = data["choices"][0]
        if choice.get("finish_reason") not in {"stop", "tool_calls"}:
            raise ProviderError("模型响应未正常结束，拒绝执行不完整调用")
        message = choice["message"]
        content = message.get("content") or ""
        if not isinstance(content, str):
            raise ProviderError("模型文本格式不支持")
        return ModelResponse(content, parse_calls(message.get("tool_calls") or []))
    except (KeyError, IndexError, TypeError, AttributeError):
        raise ProviderError("模型响应格式不符合 Chat Completions 协议") from None


class OpenAICompatibleProvider:
    def __init__(
        self, settings: ModelSettings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport

    def _payload(
        self, messages: list[Message], tools: list[dict[str, Any]], streaming: bool
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": wire_messages(messages),
            "tools": tools,
            "tool_choice": "auto",
            "stream": streaming,
        }
        if self.settings.is_deepseek:
            # M1 does not round-trip reasoning_content. Request non-thinking
            # mode explicitly rather than relying on the service's defaults.
            payload["thinking"] = {"type": "disabled"}
        return payload

    async def generate(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse:
        response: ModelResponse | None = None
        async for item in self.stream(messages, tools):
            if isinstance(item, ModelResponse):
                response = item
        if response is None:
            raise ProviderError("模型流未返回完整响应")
        return response

    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ModelResponse]:
        headers = {"Accept": "text/event-stream, application/json"}
        if self.settings.api_key:
            headers["Authorization"] = "Bearer " + self.settings.api_key
        endpoint = self.settings.base_url.rstrip("/") + "/chat/completions"
        for attempt in range(3):
            yielded = False
            try:
                async with httpx.AsyncClient(
                    headers=headers,
                    timeout=self.settings.timeout_s,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self.transport,
                ) as client:
                    async with client.stream(
                        "POST",
                        endpoint,
                        json=self._payload(messages, tools, self.settings.streaming),
                    ) as response:
                        if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                            await asyncio.sleep(0.25 * 2**attempt)
                            continue
                        if not response.is_success:
                            raise ProviderError(
                                await explain_http_error(
                                    response, deepseek=self.settings.is_deepseek
                                )
                            )
                        if "text/event-stream" not in response.headers.get("content-type", ""):
                            chunks: list[bytes] = []
                            size = 0
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > 1_048_576:
                                    raise ProviderError("模型响应超过大小上限")
                                chunks.append(chunk)
                            raw = b"".join(chunks)
                            try:
                                parsed = parse_response(json.loads(raw))
                            except (ValueError, TypeError):
                                raise ProviderError("模型服务未返回有效 JSON 响应") from None
                            if parsed.text:
                                yield parsed.text
                            yield parsed
                        else:
                            # Once an SSE response has started, a broken connection
                            # must fail this step, even if only tool fragments arrived.
                            yielded = True
                            async for item in self._events(response):
                                yielded = True
                                yield item
                        return
            except httpx.HTTPError:
                if yielded or attempt == 2:
                    raise ProviderError("模型连接失败、超时或流被中断；本轮未正常完成") from None
                await asyncio.sleep(0.25 * 2**attempt)
        raise ProviderError("模型请求重试次数耗尽")

    async def _events(self, response: httpx.Response) -> AsyncIterator[str | ModelResponse]:
        text: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish: str | None = None
        done = False
        async for value in self._sse_data(response):
            if value == "[DONE]":
                done = True
                break
            try:
                data = json.loads(value)
                choices = data.get("choices", [])
                if not choices:
                    if "error" in data:
                        raise ProviderError("模型流返回服务错误")
                    continue
                choice = choices[0]
                delta = choice.get("delta", {})
                if choice.get("finish_reason") is not None:
                    finish = choice["finish_reason"]
                content = delta.get("content")
                if content:
                    if not isinstance(content, str):
                        raise ProviderError("模型流文本格式不支持")
                    text.append(content)
                    yield content
                for patch in delta.get("tool_calls") or []:
                    index = patch.get("index")
                    if not isinstance(index, int) or not 0 <= index < 16:
                        raise ProviderError("工具流 index 无效")
                    target = calls.setdefault(
                        index, {"id": "", "function": {"name": "", "arguments": ""}}
                    )
                    if patch.get("id"):
                        target["id"] += patch["id"]
                    function = patch.get("function", {})
                    for key in ("name", "arguments"):
                        if function.get(key):
                            target["function"][key] += function[key]
            except (ValueError, TypeError, KeyError, AttributeError):
                raise ProviderError("模型流包含损坏的 JSON 或工具增量") from None
        if not done or finish not in {"stop", "tool_calls"}:
            raise ProviderError("模型流未完整结束；不会执行部分工具调用")
        yield ModelResponse("".join(text), parse_calls([calls[index] for index in sorted(calls)]))

    async def _sse_data(self, response: httpx.Response) -> AsyncIterator[str]:
        buffer = b""
        size = 0
        data: list[str] = []
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > 1_048_576:
                raise ProviderError("模型流超过大小上限")
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                try:
                    line = raw.rstrip(b"\r").decode("utf-8")
                except UnicodeDecodeError:
                    raise ProviderError("模型流不是有效 UTF-8") from None
                if line.startswith("data:"):
                    data.append(line[5:].lstrip(" "))
                elif not line and data:
                    yield "\n".join(data)
                    data.clear()
        if buffer:
            try:
                line = buffer.decode("utf-8").rstrip("\r")
            except UnicodeDecodeError:
                raise ProviderError("模型流不是有效 UTF-8") from None
            if line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
        if data:
            yield "\n".join(data)
