import httpx
import pytest

from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.models import Message
from minicodex.backend.llm.openai_compatible import OpenAICompatibleProvider, ProviderError


@pytest.mark.parametrize(
    "detail, field, expected",
    [
        ("Model Not Exist", "model", "模型名称"),
        ("Missing reasoning_content in assistant", "messages[1]", "思考内容回传"),
        ("Invalid thinking parameter", "thinking.type", "思考模式参数"),
        ("Invalid tools JSON Schema", "tools[0].function.parameters", "工具定义"),
    ],
)
async def test_400_has_actionable_diagnostic_without_raw_error(detail, field, expected):
    provider = OpenAICompatibleProvider(
        ModelSettings(
            mode="compatible",
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
            api_key="PRIVATE_KEY",
        ),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                json={
                    "error": {
                        "message": detail + " PRIVATE_KEY PRIVATE_PROMPT",
                        "param": field,
                    }
                },
            )
        ),
    )
    with pytest.raises(ProviderError) as caught:
        await provider.generate([Message("user", "PRIVATE_PROMPT")], [])
    message = str(caught.value)
    assert expected in message and field in message
    assert "PRIVATE_KEY" not in message and "PRIVATE_PROMPT" not in message


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"message": "SENSITIVE RAW DATA", "param": "SENSITIVE_SECRET"}},
        {"error": "SENSITIVE RAW DATA"},
        {"error": {"message": ["SENSITIVE RAW DATA"]}},
        {"error": {"message": "SENSITIVE RAW DATA", "param": "model.SENSITIVE_SECRET"}},
    ],
)
async def test_unknown_structured_errors_do_not_echo_private_content(body):
    provider = OpenAICompatibleProvider(
        ModelSettings(
            mode="compatible",
            base_url="http://localhost/v1",
            model="test",
        ),
        transport=httpx.MockTransport(lambda request: httpx.Response(400, json=body)),
    )
    with pytest.raises(ProviderError) as caught:
        await provider.generate([Message("user", "hello")], [])
    assert "SENSITIVE" not in str(caught.value)
    assert "HTTP 400" in str(caught.value)
