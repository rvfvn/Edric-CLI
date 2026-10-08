from types import SimpleNamespace

import pytest

from edric.contracts import MCPResult, ToolCall, ToolDefinition
from edric.dispatcher import ToolDispatcher
from edric.policy import ExecutionPolicy


class Hub:
    def __init__(self):
        self.calls = []
        self.tools = {
            "fs__write": ToolDefinition("fs__write", "write", {
                "type": "object", "properties": {"path": {"type": "string"}},
                "required": ["path"], "additionalProperties": False,
            }, "filesystem", "write_file", True),
            "fs__read": ToolDefinition("fs__read", "read", {"type": "object"},
                                       "filesystem", "read_text_file", False),
        }

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return MCPResult("ok")


async def test_denied_write_never_reaches_server(tmp_path):
    hub = Hub()
    async def deny(*args):
        return False
    dispatcher = ToolDispatcher(hub, tmp_path, ExecutionPolicy("confirm"), deny)
    result = await dispatcher.execute(ToolCall("c1", "fs__write", {"path": "a"}))
    assert result.status == "denied"
    assert hub.calls == []


async def test_invalid_arguments_never_execute_or_prompt(tmp_path):
    hub = Hub()
    async def approval(*args):
        raise AssertionError("Invalid action prompted")
    dispatcher = ToolDispatcher(hub, tmp_path, ExecutionPolicy(), approval)
    result = await dispatcher.execute(ToolCall("c1", "fs__write", {"path": 42}))
    assert result.status == "error"
    assert hub.calls == []


async def test_read_runs_without_prompt(tmp_path):
    hub = Hub()
    async def approval(*args):
        raise AssertionError("Read prompted")
    result = await ToolDispatcher(hub, tmp_path, ExecutionPolicy(), approval).execute(ToolCall("c1", "fs__read", {}))
    assert result.status == "success"
    assert len(hub.calls) == 1


async def test_auto_executes_write(tmp_path):
    hub = Hub()
    result = await ToolDispatcher(hub, tmp_path, ExecutionPolicy("auto")).execute(ToolCall("c1", "fs__write", {"path": "a"}))
    assert result.status == "success"
    assert len(hub.calls) == 1


async def test_unknown_tool_returns_observation(tmp_path):
    hub = Hub()
    result = await ToolDispatcher(hub, tmp_path, ExecutionPolicy()).execute(ToolCall("c1", "missing", {}))
    assert result.status == "error"
    assert hub.calls == []


def test_unknown_mutability_requires_confirmation():
    unknown = ToolDefinition("unknown", "unknown", {})
    assert ExecutionPolicy().requires_approval(unknown)
    assert not ExecutionPolicy("auto").requires_approval(unknown)
