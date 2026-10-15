import asyncio
import json

import httpx
import pytest

from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.models import Message
from minicodex.backend.llm.openai_compatible import OpenAICompatibleProvider, ProviderError


def settings(**kwargs):
    return ModelSettings(
        mode="compatible", base_url="http://127.0.0.1:12345/v1", model="test-model", **kwargs
    )


def final(text="done"):
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}


def event(delta, finish=None):
    return "data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]}) + "\n\n"


async def test_nonstream_response_and_payload_have_tools():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(200, json=final("hello"))

    provider = OpenAICompatibleProvider(
        settings(api_key="test-secret", streaming=False), transport=httpx.MockTransport(handler)
    )
    reply = await provider.generate([Message("user", "hi")], [{"type": "function"}])
    assert reply.text == "hello" and requests[0]["stream"] is False
    assert requests[0]["tools"] == [{"type": "function"}]


async def test_interleaved_streamed_tool_arguments_are_assembled():
    body = (
        event(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "read",
                        "function": {"name": "read_file", "arguments": '{"pa'},
                    },
                    {
                        "index": 1,
                        "id": "list",
                        "function": {"name": "list_files", "arguments": "{"},
                    },
                ]
            }
        )
        + event(
            {
                "tool_calls": [
                    {"index": 1, "function": {"arguments": "}"}},
                    {"index": 0, "function": {"arguments": 'th":"README.md"}'}},
                ]
            },
            "tool_calls",
        )
        + "data: [DONE]\n\n"
    )
    provider = OpenAICompatibleProvider(
        settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}
            )
        ),
    )
    response = await provider.generate([Message("user", "read")], [])
    assert [c.call_id for c in response.tool_calls] == ["read", "list"]
    assert response.tool_calls[0].arguments == {"path": "README.md"}


@pytest.mark.parametrize(
    "body",
    [
        event({"content": "unfinished"}),
        event(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "c",
                        "function": {
                            "name": "list_files",
                            "arguments": "broken",
                        },
                    }
                ]
            },
            "tool_calls",
        )
        + "data: [DONE]\n\n",
        event({"content": "truncated"}, "length") + "data: [DONE]\n\n",
        "data: not-json\n\ndata: [DONE]\n\n",
    ],
)
async def test_incomplete_malformed_or_truncated_stream_is_rejected(body):
    provider = OpenAICompatibleProvider(
        settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}
            )
        ),
    )
    with pytest.raises(ProviderError):
        await provider.generate([Message("user", "hello")], [])


async def test_rate_limit_retry_and_error_redaction():
    attempts = []

    def handler(request):
        attempts.append(request)
        return (
            httpx.Response(429, json={"secret": "SENSITIVE"})
            if len(attempts) < 3
            else httpx.Response(200, json=final())
        )

    provider = OpenAICompatibleProvider(settings(), transport=httpx.MockTransport(handler))
    assert (await provider.generate([Message("user", "hi")], [])).text == "done"
    assert len(attempts) == 3
    bad = OpenAICompatibleProvider(
        settings(api_key="MY_PRIVATE_KEY"),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, text="MY_PRIVATE_KEY SENSITIVE RESPONSE")
        ),
    )
    with pytest.raises(ProviderError) as caught:
        await bad.generate([Message("user", "hi")], [])
    assert "MY_PRIVATE_KEY" not in str(caught.value)
    assert "SENSITIVE" not in str(caught.value)
    assert "MY_PRIVATE_KEY" not in repr(bad.settings)


async def test_disconnect_after_tool_fragments_does_not_reconnect():
    attempts = []

    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield event({"tool_calls": [{"index": 0, "id": "c"}]}).encode()
            raise httpx.ReadError("broken")

    def handler(request):
        attempts.append(request)
        return httpx.Response(200, stream=Broken(), headers={"content-type": "text/event-stream"})

    provider = OpenAICompatibleProvider(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError):
        await provider.generate([Message("user", "hi")], [])
    assert len(attempts) == 1


async def test_waiting_http_request_is_cancellable():
    started = asyncio.Event()

    async def handler(request):
        started.set()
        await asyncio.sleep(100)
        return httpx.Response(200, json=final())

    provider = OpenAICompatibleProvider(settings(), transport=httpx.MockTransport(handler))
    task = asyncio.create_task(provider.generate([Message("user", "hello")], []))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize(
    "url", ["http://remote.example/v1", "https://key@service/v1", "https://service/v1?key=secret"]
)
def test_model_settings_reject_unsafe_urls(url):
    with pytest.raises(ValueError):
        ModelSettings(mode="compatible", base_url=url, model="test")


@pytest.mark.parametrize("streaming", [True, False])
async def test_deepseek_explicitly_disables_thinking_on_every_tool_step(tmp_path, streaming):
    from threading import Event

    from minicodex.backend.core.history import validate_history
    from minicodex.backend.core.models import ThreadState, TurnState, TurnStatus
    from minicodex.backend.core.runtime import AgentRuntime

    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["thinking"] == {"type": "disabled"}
        if len(requests) == 1:
            response = {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "list",
                                    "type": "function",
                                    "function": {
                                        "name": "list_files",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                    }
                ]
            }
        else:
            assert body["messages"][-1]["role"] == "tool"
            response = final("non-thinking tool loop completed")
        return httpx.Response(200, json=response)

    provider = OpenAICompatibleProvider(
        ModelSettings(
            mode="compatible",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-flash",
            streaming=streaming,
        ),
        transport=httpx.MockTransport(handler),
    )
    thread = ThreadState(tmp_path)
    turn = TurnState(thread.thread_id)

    async def emit(event):
        pass

    await AgentRuntime(provider).run_turn(thread, turn, "list files", Event(), emit)
    assert turn.status == TurnStatus.COMPLETED
    assert len(requests) == 2
    validate_history(thread.history)


@pytest.mark.parametrize(
    "url", ["https://api.openai.com/v1", "https://api.deepseek.com.example/v1"]
)
def test_other_hosts_do_not_receive_deepseek_parameter(url):
    provider = OpenAICompatibleProvider(
        ModelSettings(mode="compatible", base_url=url, model="test")
    )
    assert "thinking" not in provider._payload([Message("user", "hi")], [], True)
