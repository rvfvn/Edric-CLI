"""Optional provider integration tests with HTTP mocks, never live inference."""

import asyncio
import json

import httpx
import pytest

from edric.agent import Agent
from edric.contracts import ToolDefinition, ToolResult
from edric.providers import ProviderError, ProviderUnavailableError, create_provider
from edric.providers import ollama_provider as module

TOOLS = [ToolDefinition("filesystem__read_text_file", "Read a file", {"type": "object"})]


def tool_call(arguments=None):
    return {"function": {"name": TOOLS[0].name, "arguments": arguments if arguments is not None else {"path": "a.py"}}}


def reply(content="", calls=None, **extra):
    return {"message": {"role": "assistant", "content": content, "tool_calls": calls or []},
            "done": True, "done_reason": "stop", **extra}


def streamed(chunks):
    return httpx.Response(200, text="\n".join(json.dumps(chunk) for chunk in chunks) + "\n")


@pytest.fixture
def provider_factory(monkeypatch):
    real_client = httpx.AsyncClient

    def factory(handler, **kwargs):
        monkeypatch.setattr(module.httpx, "AsyncClient", lambda **options: real_client(
            transport=httpx.MockTransport(handler), **options))
        return module.OllamaProvider(**kwargs)
    return factory


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", ["success", "error", "denied"])
async def test_agent_tool_result_continuation_without_loop_changes(provider_factory, tmp_path, stream, status):
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert str(request.url) == "http://localhost:11434/api/chat"
        if len(requests) == 1:
            data = reply("Inspecting", [tool_call()])
            data["message"]["thinking"] = "Need file contents"
        else:
            messages = payload["messages"]
            assert messages[-2]["thinking"] == "Need file contents"
            assert messages[-2]["tool_calls"][0]["function"]["arguments"] == {"path": "a.py"}
            assert "id" not in messages[-2]["tool_calls"][0]
            assert messages[-1]["tool_name"] == TOOLS[0].name
            assert "tool_call_id" not in messages[-1]
            assert json.loads(messages[-1]["content"]) == {"status": status, "result": "observed contents"}
            data = reply("Finished")
        return streamed([data]) if stream else httpx.Response(200, json=data)

    class Dispatcher:
        tools = {TOOLS[0].name: TOOLS[0]}
        calls = []

        async def execute(self, call):
            self.calls.append(call)
            return ToolResult(call.id, call.name, "observed contents", status)

    provider = provider_factory(handler, stream=stream)
    displayed = []
    try:
        dispatcher = Dispatcher()
        outcome = await Agent(provider, dispatcher).run("Inspect a.py", tmp_path, displayed.append)
        assert outcome.status == "completed"
        assert outcome.turns == 2
        assert len(outcome.tool_results) == 1
        assert dispatcher.calls[0].arguments == {"path": "a.py"}
        assert displayed == ["Inspecting", "Finished"]
        assert requests[0]["stream"] is stream
        assert requests[0]["tools"][0]["function"]["parameters"] == TOOLS[0].input_schema
    finally:
        await provider.close()
    assert provider._client.is_closed


async def test_stream_accumulates_text_and_distinct_calls_before_continuation(provider_factory):
    chunks = [
        {"message": {"content": "Inspect ", "thinking": "Need "}, "done": False},
        {"message": {"content": "files", "thinking": "context", "tool_calls": [tool_call()]}, "done": False},
        {"message": {"tool_calls": [tool_call({"path": "b.py"})]}, "done": False},
        {"message": {"content": ""}, "done": True, "done_reason": "stop"},
    ]
    provider = provider_factory(lambda request: streamed(chunks))
    displayed = []
    try:
        turn = await provider.complete([], TOOLS, displayed.append)
        assert turn.content == "Inspect files"
        assert displayed == ["Inspect ", "files"]
        assert [call.arguments for call in turn.tool_calls] == [{"path": "a.py"}, {"path": "b.py"}]
        assert len({call.id for call in turn.tool_calls}) == 2
        native = module._messages([turn.raw_message, *[
            {"role": "tool", "tool_call_id": call.id, "content": call.arguments["path"]}
            for call in turn.tool_calls
        ]])
        assert native[0]["thinking"] == "Need context"
        assert [message["tool_name"] for message in native[1:]] == [TOOLS[0].name, TOOLS[0].name]
        assert [message["content"] for message in native[1:]] == ["a.py", "b.py"]
    finally:
        await provider.close()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("invalid", ['{"path":', ["a.py"], {"value": float("nan")}])
async def test_invalid_call_rejects_whole_turn(provider_factory, stream, invalid):
    data = reply(calls=[tool_call(), tool_call(invalid)])
    provider = provider_factory(lambda request: streamed([data]) if stream else httpx.Response(200, text=json.dumps(data)),
                                stream=stream)
    try:
        with pytest.raises(ProviderError, match="No tools"):
            await provider.complete([], TOOLS)
    finally:
        await provider.close()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("completion", [{"done": False}, {"done": True, "done_reason": "length"}])
async def test_incomplete_turn_does_not_return_executable_calls(provider_factory, stream, completion):
    data = reply(calls=[tool_call()], **completion)
    provider = provider_factory(lambda request: streamed([data]) if stream else httpx.Response(200, json=data),
                                stream=stream)
    try:
        with pytest.raises(ProviderError, match="No tools"):
            await provider.complete([], TOOLS)
    finally:
        await provider.close()


@pytest.mark.parametrize("failure", ["connection", "timeout", "404", "500", "invalid-json", "error"])
async def test_errors_are_useful_without_exposing_response_bodies(provider_factory, failure):
    def handler(request):
        if failure == "connection":
            raise httpx.ConnectError("SECRET_SERVER_DETAIL", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("SECRET_SERVER_DETAIL", request=request)
        if failure.isdigit():
            return httpx.Response(int(failure), text="SECRET_SERVER_DETAIL")
        if failure == "invalid-json":
            return httpx.Response(200, text="SECRET_SERVER_DETAIL")
        return httpx.Response(200, json={"error": "SECRET_SERVER_DETAIL"})

    provider = provider_factory(handler, stream=False)
    try:
        with pytest.raises(ProviderError) as caught:
            await provider.complete([], [])
        assert "SECRET_SERVER_DETAIL" not in str(caught.value)
        if failure == "connection":
            assert "Start its server" in str(caught.value)
        if failure == "404":
            assert "installed" in str(caught.value)
    finally:
        await provider.close()


async def test_cancellation_closes_stream_without_executing_pending_call(provider_factory):
    class InterruptedStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield (json.dumps({"message": {"tool_calls": [tool_call()]}, "done": False}) + "\n").encode()
            raise asyncio.CancelledError()

        async def aclose(self):
            self.closed = True

    stream = InterruptedStream()
    provider = provider_factory(lambda request: httpx.Response(200, stream=stream))
    try:
        with pytest.raises(asyncio.CancelledError):
            await provider.complete([], TOOLS)
        assert stream.closed
    finally:
        await provider.close()


async def test_factory_enables_only_explicit_ollama_selection(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "local-test-model")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11435/")
    provider = create_provider("ollama", stream=False)
    try:
        assert provider.model == "local-test-model"
        assert provider.base_url == "http://localhost:11435"
        assert provider.stream is False
    finally:
        await provider.close()
    provider = create_provider("ollama", model="override")
    try:
        assert provider.model == "override"
    finally:
        await provider.close()
    monkeypatch.setenv("OLLAMA_MODEL", "")
    provider = create_provider("ollama")
    try:
        assert provider.model == module.DEFAULT_MODEL
    finally:
        await provider.close()
    with pytest.raises(ProviderUnavailableError, match="nonempty"):
        create_provider("ollama", model=" ")
