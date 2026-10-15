import json
import re

import httpx


async def explain_http_error(response: httpx.Response, *, deepseek: bool) -> str:
    """Classify bounded structured error data without publishing its raw text."""
    status = response.status_code
    prefix = f"模型服务 HTTP {status}"
    if status == 401:
        return prefix + "：认证失败，请检查 API Key 是否属于所选服务。"
    if status == 402:
        return prefix + "：服务账户余额不足。"
    if status == 429:
        return prefix + "：服务限流，请稍后重试。"
    error: dict[str, object] = {}
    try:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > 8192:
                break
            body.extend(chunk)
        value = json.loads(body)
        if isinstance(value, dict) and isinstance(value.get("error"), dict):
            error = value["error"]
    except (ValueError, httpx.HTTPError):
        pass
    text = " ".join(
        value
        for key in ("message", "code", "type", "param")
        if isinstance(value := error.get(key), str)
    ).casefold()
    # Only fixed explanations reach the UI; error text may echo a prompt or key.
    if "reasoning_content" in text:
        hint = "服务要求思考内容回传，与当前非思考模式请求不匹配；请新建会话并核对模型。"
    elif "thinking" in text:
        hint = "服务拒绝了思考模式参数；请检查模型和接口是否支持该参数。"
    elif "model" in text and any(
        word in text
        for word in (
            "not exist",
            "does not exist",
            "not found",
            "invalid",
            "unknown",
            "unsupported",
            "unavailable",
            "retired",
            "不存在",
            "不可用",
        )
    ):
        hint = "模型名称不被服务接受，请核对“模型名称”一栏。"
        if deepseek:
            hint += "DeepSeek 官方配置可使用 deepseek-flash。"
    elif any(word in text for word in ("schema", "tools", "tool_choice", "function", "parameters")):
        hint = "服务拒绝了工具定义或参数格式；需核对工具调用协议。"
    elif "messages" in text or "message role" in text:
        hint = "对话消息格式被拒绝；请新建会话后重试。"
    elif "api_key" in text or "api key" in text or "authentication" in text:
        hint = "认证信息被服务拒绝，请检查 API Key 的格式和所属服务。"
    elif status == 404:
        hint = "接口路径或模型不存在；Base URL 不应包含 /chat/completions。"
    elif status in {400, 422}:
        hint = "请求格式或参数被拒绝，服务未返回可识别的错误类别；请核对 Base URL 和模型名称。"
    else:
        hint = "服务请求失败，请稍后重试。"
    field = error.get("param")
    known_fields = {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "thinking",
        "stream",
        "max_tokens",
        "role",
        "content",
        "tool_calls",
        "tool_call_id",
        "id",
        "type",
        "name",
        "function",
        "parameters",
        "properties",
        "required",
        "additionalProperties",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "path",
        "pattern",
        "glob",
        "root",
        "max_results",
        "case_sensitive",
        "start_line",
        "end_line",
    }
    if isinstance(field, str) and re.fullmatch(r"[a-zA-Z0-9_.\[\]-]{1,80}", field):
        tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", field)
        if tokens and all(token in known_fields for token in tokens):
            hint += f"错误字段：{field}。"
    return prefix + "：" + hint
