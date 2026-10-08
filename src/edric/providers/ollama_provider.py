"""Optional Ollama adapter; it never installs or downloads a model.

Adapted from Smruthi Sundar's part4/providers/ollama.py on
feature/part4-external-mcp-providers
(02a544b), retaining its /api/chat request flow behind Edric's async contract.
"""

import json
import os
from uuid import uuid4

import httpx

from edric.contracts import JSON, ModelTurn, TextCallback, ToolCall, ToolDefinition

from .base import ProviderError, ProviderUnavailableError

DEFAULT_MODEL = "qwen2.5-coder:7b"


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON value")


def _arguments(value) -> JSON:
    if isinstance(value, str):
        value = json.loads(value, parse_constant=_reject_constant)
    if not isinstance(value, dict):
        raise ValueError("Tool arguments must be an object")
    # Reject non-finite values in native argument objects as well as JSON strings.
    json.dumps(value, allow_nan=False)
    return value


def _messages(messages: list[JSON]) -> list[JSON]:
    """Translate Edric call IDs into Ollama's tool-name continuation format."""
    translated: list[JSON] = []
    names: dict[str, str] = {}
    for message in messages:
        role = message["role"]
        native: JSON = {"role": role, "content": message.get("content") or ""}
        if role == "assistant":
            if message.get("thinking"):
                native["thinking"] = message["thinking"]
            calls = []
            for call in message.get("tool_calls") or []:
                function = call["function"]
                name = function["name"]
                call_id = call["id"]
                if not isinstance(name, str) or not name or call_id in names:
                    raise ValueError("Invalid conversation tool call")
                names[call_id] = name
                calls.append({"type": "function", "function": {
                    "name": name, "arguments": _arguments(function["arguments"]),
                }})
            if calls:
                native["tool_calls"] = calls
        elif role == "tool":
            native["tool_name"] = names[message["tool_call_id"]]
        translated.append(native)
    return translated


def _model_turn(content: str, thinking: str, raw_calls: list[JSON]) -> ModelTurn:
    """Validate every call before making any calls in this turn executable."""
    calls: list[ToolCall] = []
    continuation = []
    for raw in raw_calls:
        if not isinstance(raw, dict) or raw.get("type", "function") != "function":
            raise ValueError("Invalid tool call")
        function = raw["function"]
        name = function["name"]
        if not isinstance(name, str) or not name:
            raise ValueError("Invalid tool name")
        arguments = _arguments(function["arguments"])
        call_id = "ollama_" + uuid4().hex
        calls.append(ToolCall(call_id, name, arguments))
        continuation.append({"id": call_id, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments, allow_nan=False),
        }})
    raw_message: JSON = {"role": "assistant", "content": content}
    if thinking:
        raw_message["thinking"] = thinking
    if continuation:
        raw_message["tool_calls"] = continuation
    return ModelTurn(content, calls, raw_message)


def _read_chunk(data) -> tuple[str, str, list[JSON]]:
    if not isinstance(data, dict) or data.get("error"):
        raise ProviderError("Ollama returned an error. No tools from this turn were executed.")
    message = data.get("message", {})
    if not isinstance(message, dict) or message.get("role", "assistant") != "assistant":
        raise ValueError("Invalid assistant message")
    content = message.get("content", "")
    thinking = message.get("thinking", "")
    calls = message.get("tool_calls") or []
    if not isinstance(content, str) or not isinstance(thinking, str) or not isinstance(calls, list):
        raise ValueError("Invalid assistant response")
    return content, thinking, calls


def _check_done(data: JSON) -> None:
    if data.get("done") is not True or data.get("done_reason") not in {None, "stop"}:
        raise ProviderError(
            "Ollama returned an incomplete response. No tools from this turn were executed."
        )


class OllamaProvider:
    def __init__(self, model: str | None = None, stream: bool = True,
                 base_url: str | None = None, timeout: float = 120.0):
        self.model = model if model is not None else (os.environ.get("OLLAMA_MODEL") or DEFAULT_MODEL)
        self.base_url = (base_url if base_url is not None else os.environ.get(
            "OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        if not self.model.strip():
            raise ProviderUnavailableError("Set a nonempty Ollama model using --model or OLLAMA_MODEL.")
        url = httpx.URL(self.base_url)
        if url.scheme not in {"http", "https"} or not url.host:
            raise ProviderUnavailableError("OLLAMA_HOST must be an HTTP or HTTPS server address.")
        self.stream = stream
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0))

    async def complete(
        self,
        messages: list[JSON],
        tools: list[ToolDefinition],
        on_text: TextCallback | None = None,
    ) -> ModelTurn:
        try:
            request: JSON = {"model": self.model, "messages": _messages(messages), "stream": self.stream}
            if tools:
                request["tools"] = [{"type": "function", "function": {
                    "name": tool.name, "description": tool.description, "parameters": tool.input_schema,
                }} for tool in tools]
            if self.stream:
                return await self._read_stream(request, on_text)
            response = await self._client.post(f"{self.base_url}/api/chat", json=request)
            response.raise_for_status()
            data = response.json()
            content, thinking, calls = _read_chunk(data)
            _check_done(data)
            turn = _model_turn(content, thinking, calls)
            if content and on_text:
                on_text(content)
            return turn
        except httpx.TimeoutException:
            raise ProviderError("Ollama timed out. No tools from the unfinished turn were executed.") from None
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                detail = "Check OLLAMA_HOST and select an installed tool-capable model with --model."
            else:
                detail = "Check the Ollama server and selected model."
            raise ProviderError(f"Ollama request failed (HTTP {error.response.status_code}). {detail}") from None
        except httpx.RequestError:
            raise ProviderUnavailableError(
                "Could not connect to Ollama. Start its server and check OLLAMA_HOST; "
                "Groq remains available through --provider groq."
            ) from None
        except (ValueError, TypeError, KeyError):
            raise ProviderError(
                "Ollama returned invalid response or tool data. No tools from this turn were executed."
            ) from None

    async def _read_stream(self, request: JSON, on_text: TextCallback | None) -> ModelTurn:
        content: list[str] = []
        thinking: list[str] = []
        calls: list[JSON] = []
        async with self._client.stream("POST", f"{self.base_url}/api/chat", json=request) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line, parse_constant=_reject_constant)
                text, thought, new_calls = _read_chunk(data)
                content.append(text)
                thinking.append(thought)
                calls.extend(new_calls)
                if text and on_text:
                    on_text(text)
                if data.get("done") is True:
                    _check_done(data)
                    return _model_turn("".join(content), "".join(thinking), calls)
        raise ProviderError("Ollama's stream ended early. No tools from this turn were executed.")

    async def close(self) -> None:
        await self._client.aclose()
