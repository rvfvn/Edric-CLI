"""Provider boundary tests; these are simulated API responses, not live evidence."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from groq import APIConnectionError, AuthenticationError, RateLimitError
from groq.types.chat import ChatCompletion, ChatCompletionChunk

from edric.contracts import ToolDefinition
from edric.providers import ProviderError, ProviderUnavailableError, create_provider
from edric.providers import groq_provider as module


class FakeStream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            value = next(self.chunks)
        except StopIteration:
            raise StopAsyncIteration
        if isinstance(value, Exception):
            raise value
        return value

    async def close(self):
        self.closed = True


def chunk(content=None, calls=None, finish=None):
    return ChatCompletionChunk(
        id="chunk-test", created=0, model=module.DEFAULT_MODEL, object="chat.completion.chunk",
        choices=[{"index": 0, "delta": {"content": content, "tool_calls": calls}, "finish_reason": finish}],
    )


def fragment(index, call_id=None, name=None, arguments=None):
    return {
        "index": index, "id": call_id, "type": "function" if call_id else None,
        "function": {"name": name, "arguments": arguments},
    }


@pytest.fixture
def client(monkeypatch):
    fake = NS(chat=NS(completions=NS(create=AsyncMock())), close=AsyncMock())
    factory = lambda **kwargs: fake
    monkeypatch.setattr(module, "AsyncGroq", factory)
    return fake


@pytest.mark.asyncio
async def test_stream_preserves_interleaved_calls_and_complete_arguments(client):
    response = FakeStream([
        chunk("I will inspect "),
        chunk("both files.", [fragment(0, "call_a", "filesystem__read_text_file", '{"path":')]),
        chunk(calls=[fragment(1, "call_b", "filesystem__read_text_file", '{"path":"b.py"}')]),
        chunk(calls=[fragment(0, arguments='"a.py"}')]),
        chunk(finish="tool_calls"),
    ])
    client.chat.completions.create.return_value = response
    provider = module.GroqProvider(api_key="fake-key")
    displayed = []
    tools = [ToolDefinition("filesystem__read_text_file", "Read file", {"type": "object"})]
    turn = await provider.complete([{"role": "user", "content": "Inspect files"}], tools, displayed.append)
    assert turn.content == "I will inspect both files."
    assert displayed == ["I will inspect ", "both files."]
    assert [(c.id, c.arguments) for c in turn.tool_calls] == [
        ("call_a", {"path": "a.py"}), ("call_b", {"path": "b.py"})
    ]
    assert turn.raw_message["tool_calls"][0]["function"]["arguments"] == '{"path":"a.py"}'
    assert turn.raw_message["tool_calls"][1]["id"] == "call_b"
    assert response.closed
    request = client.chat.completions.create.call_args.kwargs
    assert request["parallel_tool_calls"] is False
    assert request["tools"][0]["function"]["name"] == tools[0].name
    await provider.close()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ['{"path":', '["file.py"]', '{"value":NaN}'])
async def test_invalid_arguments_reject_entire_turn(client, invalid):
    response = FakeStream([
        chunk(calls=[fragment(0, "call_good", "read_file", '{"path":"good.py"}')]),
        chunk(calls=[fragment(1, "call_bad", "write_file", invalid)]),
        chunk(finish="tool_calls"),
    ])
    client.chat.completions.create.return_value = response
    with pytest.raises(ProviderError, match="No tools"):
        await module.GroqProvider(api_key="fake-key").complete([], [])
    assert response.closed


@pytest.mark.asyncio
async def test_truncated_stream_does_not_return_executable_calls(client):
    response = FakeStream([
        chunk(calls=[fragment(0, "call_a", "write_file", '{"path":"a.py"}')]),
        chunk(finish="length"),
    ])
    client.chat.completions.create.return_value = response
    with pytest.raises(ProviderError, match="incomplete"):
        await module.GroqProvider(api_key="fake-key").complete([], [])
    assert response.closed


@pytest.mark.asyncio
async def test_nonstream_raw_message_supports_continuation(client):
    client.chat.completions.create.return_value = ChatCompletion(
        id="test", created=0, model=module.DEFAULT_MODEL, object="chat.completion",
        choices=[{
            "index": 0, "finish_reason": "tool_calls",
            "message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_a", "type": "function",
                "function": {"name": "read_file", "arguments": '{"path":"a.py"}'},
            }]},
        }],
    )
    turn = await module.GroqProvider(api_key="fake-key", stream=False).complete([], [])
    assert turn.tool_calls[0].id == "call_a"
    assert turn.raw_message == {
        "role": "assistant", "content": None, "tool_calls": [{
            "id": "call_a", "type": "function",
            "function": {"name": "read_file", "arguments": '{"path":"a.py"}'},
        }],
    }


@pytest.mark.asyncio
async def test_authentication_error_does_not_expose_body_or_key(client):
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(401, request=request)
    client.chat.completions.create.side_effect = AuthenticationError(
        "fake-secret-key in failure", response=response, body={"secret": "fake-secret-key"}
    )
    with pytest.raises(ProviderError) as caught:
        await module.GroqProvider(api_key="fake-secret-key").complete([], [])
    assert "fake-secret-key" not in str(caught.value)
    assert "rejected" in str(caught.value)


@pytest.mark.asyncio
async def test_stream_failure_closes_stream_and_returns_no_turn(client):
    response = FakeStream([
        chunk(calls=[fragment(0, "call_a", "write_file", "{}")]),
        APIConnectionError(request=httpx.Request("POST", "https://api.groq.com")),
    ])
    client.chat.completions.create.return_value = response
    with pytest.raises(ProviderError, match="connect"):
        await module.GroqProvider(api_key="fake-key").complete([], [])
    assert response.closed


@pytest.mark.asyncio
async def test_rate_limit_retries_once_with_short_explicit_delay(client, monkeypatch):
    request = httpx.Request("POST", "https://api.groq.com")
    failure = RateLimitError(
        "rate limit", response=httpx.Response(429, headers={"retry-after": "2"}, request=request), body=None
    )
    response = FakeStream([chunk("Done", finish="stop")])
    client.chat.completions.create.side_effect = [failure, response]
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    turn = await module.GroqProvider(api_key="fake-key").complete([], [])
    assert turn.content == "Done"
    assert client.chat.completions.create.await_count == 2
    sleep.assert_awaited_once_with(2.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", ["61", "NaN", "invalid", "-1"])
async def test_long_or_invalid_rate_limit_does_not_retry(client, monkeypatch, delay):
    failure = RateLimitError(
        "rate limit", response=httpx.Response(
            429, headers={"retry-after": delay}, request=httpx.Request("POST", "https://api.groq.com")
        ), body=None,
    )
    client.chat.completions.create.side_effect = failure
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    with pytest.raises(ProviderError, match="rate limit"):
        await module.GroqProvider(api_key="fake-key").complete([], [])
    assert client.chat.completions.create.await_count == 1
    sleep.assert_not_awaited()


def test_missing_key_gives_setup_instruction(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ProviderUnavailableError, match="edric setup"):
        create_provider("groq")


@pytest.mark.parametrize("name", ["bedrock", "aws", "ollama"])
def test_skeleton_selection_is_explicitly_unavailable(name):
    with pytest.raises(ProviderUnavailableError, match="not implemented"):
        create_provider(name)


def test_factory_honors_model_environment(client, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.setenv("GROQ_MODEL", "alternate-model")
    assert create_provider("groq").model == "alternate-model"
    assert create_provider("groq", model="override").model == "override"


async def test_rate_limit_message_interval_is_honored(client, monkeypatch):
    failure = RateLimitError(
        "Token limit reached. Please try again in 40s.",
        response=httpx.Response(429, request=httpx.Request("POST", "https://api.groq.com")), body=None,
    )
    client.chat.completions.create.side_effect = [failure, FakeStream([chunk("Done", finish="stop")])]
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    events = []
    provider = module.GroqProvider(api_key="fake-key", on_event=lambda name, data: events.append((name, data)))
    assert (await provider.complete([], [])).content == "Done"
    sleep.assert_awaited_once_with(40.0)
    assert events[0][0] == "provider_wait"


async def test_repeated_rate_limit_retries_are_bounded(client, monkeypatch):
    failure = RateLimitError("Rate limit", response=httpx.Response(
        429, headers={"retry-after": "2"}, request=httpx.Request("POST", "https://api.groq.com")), body=None)
    client.chat.completions.create.side_effect = failure
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    with pytest.raises(ProviderError, match="rate limit"):
        await module.GroqProvider(api_key="fake-key").complete([], [])
    assert client.chat.completions.create.await_count == 3
