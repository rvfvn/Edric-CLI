"""Groq local tool calling: Edric retains ownership of tool execution."""

import asyncio
import json
import math
import os
import re
from typing import Any

from groq import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncGroq,
    AuthenticationError,
    RateLimitError,
)

from edric.contracts import EventCallback, JSON, ModelTurn, TextCallback, ToolCall, ToolDefinition

from .base import ProviderError, ProviderUnavailableError

DEFAULT_MODEL = "openai/gpt-oss-120b"


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON value")


def _model_turn(content: str, raw_calls: list[JSON], finish_reason: str | None) -> ModelTurn:
    """Validate the entire response before making any calls executable."""
    if finish_reason not in {"stop", "tool_calls"}:
        raise ProviderError(
            "Groq returned an incomplete response. No tools from this turn were executed; "
            "try a smaller task or increase the output-token limit."
        )
    if finish_reason == "tool_calls" and not raw_calls:
        raise ProviderError("Groq ended with tool calls but supplied none. No tools were executed.")
    calls: list[ToolCall] = []
    ids: set[str] = set()
    for raw in raw_calls:
        call_id = raw.get("id")
        function = raw.get("function", {})
        name = function.get("name")
        arguments = function.get("arguments")
        if (
            raw.get("type") != "function"
            or not isinstance(call_id, str)
            or not call_id
            or call_id in ids
            or not isinstance(name, str)
            or not name
            or not isinstance(arguments, str)
        ):
            raise ProviderError("Groq returned an invalid tool call. No tools from this turn were executed.")
        try:
            parsed = json.loads(arguments, parse_constant=_reject_constant)
        except (ValueError, TypeError):
            raise ProviderError(
                "Groq returned invalid JSON tool arguments. No tools from this turn were executed."
            ) from None
        if not isinstance(parsed, dict):
            raise ProviderError(
                "Groq tool arguments must be a JSON object. No tools from this turn were executed."
            )
        ids.add(call_id)
        calls.append(ToolCall(id=call_id, name=name, arguments=parsed))
    raw_message: JSON = {"role": "assistant", "content": content or None}
    if raw_calls:
        raw_message["tool_calls"] = raw_calls
    return ModelTurn(content=content, tool_calls=calls, raw_message=raw_message)


class GroqProvider:
    """Official asynchronous SDK with complete streamed tool-call assembly."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        stream: bool = True,
        timeout: float = 60.0,
        on_event: EventCallback | None = None,
    ):
        key = api_key if api_key is not None else os.environ.get("GROQ_API_KEY")
        if not key or not key.strip():
            raise ProviderUnavailableError(
                "Groq needs GROQ_API_KEY. Create a Free Plan key at "
                "https://console.groq.com/keys and use 'edric setup'."
            )
        if not model.strip():
            raise ProviderUnavailableError("Set a nonempty Groq model ID.")
        self.model = model
        self.stream = stream
        self.on_event = on_event
        # Retry policy lives here; SDK retries would otherwise multiply requests.
        self._client = AsyncGroq(api_key=key.strip(), timeout=timeout, max_retries=0)

    async def close(self) -> None:
        await self._client.close()

    async def complete(
        self,
        messages: list[JSON],
        tools: list[ToolDefinition],
        on_text: TextCallback | None = None,
    ) -> ModelTurn:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": self.stream,
            "temperature": 0.2,
            "max_completion_tokens": 1024,
        }
        if tools:
            request["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ]
            request["tool_choice"] = "auto"
            request["parallel_tool_calls"] = False
        try:
            response = await self._request(request)
            if self.stream:
                return await self._read_stream(response, on_text)
            if not response.choices:
                raise ProviderError("Groq returned no response choices.")
            choice = response.choices[0]
            message = choice.message
            content = message.content or ""
            raw_calls = [
                {
                    "id": call.id,
                    "type": call.type,
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in message.tool_calls or []
            ]
            turn = _model_turn(content, raw_calls, choice.finish_reason)
            if content and on_text:
                on_text(content)
            return turn
        except AuthenticationError:
            raise ProviderError("Groq rejected the API key. Check your local GROQ_API_KEY.") from None
        except RateLimitError:
            raise ProviderError(
                "Groq's rate limit was reached. Wait and retry a smaller task; "
                "no paid-provider fallback was used."
            ) from None
        except APITimeoutError:
            raise ProviderError("Groq timed out. No tools from the unfinished turn were executed.") from None
        except APIConnectionError:
            raise ProviderError("Could not connect to Groq. Check your internet connection.") from None
        except APIStatusError as error:
            if error.status_code == 400:
                detail = "Check the model, tool schemas, and conversation; try a smaller task."
            elif error.status_code in {403, 404}:
                detail = "Check that the configured model is available to your Groq account."
            else:
                detail = "Try again later."
            raise ProviderError(f"Groq request failed (HTTP {error.status_code}). {detail}") from None
        except APIError as error:
            if "Failed to parse tool call arguments as JSON" in str(error):
                raise ProviderError(
                    "Groq could not parse the model's tool-call arguments as JSON. "
                    "No tools from this turn were executed. Try a smaller task or another tool-capable model."
                ) from None
            raise ProviderError("Groq returned an API error. No tools from this turn were executed.") from None

    async def _request(self, request: dict[str, Any]) -> Any:
        for attempt in range(3):
            try:
                return await self._client.chat.completions.create(**request)
            except RateLimitError as error:
                # Retry only a rejected inference request, never an executed tool.
                try:
                    delay = float(error.response.headers.get("retry-after", "nan"))
                except ValueError:
                    delay = math.nan
                if not math.isfinite(delay):
                    # Some Groq gateways specify the interval only in the message.
                    match = re.search(r"try again in ([0-9.]+)(ms|s|m)\b", str(error), re.IGNORECASE)
                    if match:
                        try:
                            delay = float(match.group(1)) * {"ms": .001, "s": 1, "m": 60}[match.group(2).lower()]
                        except ValueError:
                            delay = math.nan
                if attempt >= 2 or not math.isfinite(delay) or not 0 <= delay <= 60:
                    raise
                if self.on_event:
                    self.on_event("provider_wait", {"seconds": round(delay, 1), "reason": "Groq Free Plan rate limit", "retry": attempt + 1})
                await asyncio.sleep(delay)
        raise AssertionError("Unreachable retry state")

    async def _read_stream(self, response: Any, on_text: TextCallback | None) -> ModelTurn:
        content: list[str] = []
        calls: dict[int, JSON] = {}
        finish_reason: str | None = None
        try:
            async for chunk in response:
                if not chunk.choices:
                    continue  # The optional final usage chunk contains no choice.
                choice = chunk.choices[0]
                delta = choice.delta
                if delta.content:
                    content.append(delta.content)
                    if on_text:
                        on_text(delta.content)
                for fragment in delta.tool_calls or []:
                    index = fragment.index
                    if not isinstance(index, int) or index < 0 or index > 127:
                        raise ProviderError("Groq returned an invalid streamed tool call.")
                    call = calls.setdefault(
                        index,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if fragment.id:
                        call["id"] += fragment.id
                    if fragment.type:
                        call["type"] = fragment.type
                    if fragment.function:
                        if fragment.function.name:
                            call["function"]["name"] += fragment.function.name
                        if fragment.function.arguments:
                            call["function"]["arguments"] += fragment.function.arguments
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
        finally:
            await response.close()
        return _model_turn("".join(content), [calls[i] for i in sorted(calls)], finish_reason)
