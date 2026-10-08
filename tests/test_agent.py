import asyncio
import json
from pathlib import Path

import pytest

from edric.agent import Agent
from edric.contracts import ModelTurn, ToolCall, ToolDefinition, ToolResult


class ScriptedProvider:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.seen = []

    async def complete(self, messages, tools, on_text=None):
        self.seen.append(list(messages))
        return next(self.turns)


class Dispatcher:
    tools = {"test__read": ToolDefinition("test__read", "read", {"type": "object"})}

    def __init__(self, status="success"):
        self.calls = []
        self.status = status

    async def execute(self, call):
        self.calls.append(call)
        return ToolResult(call.id, call.name, "observed file contents", self.status)


@pytest.mark.parametrize("status", ["success", "error", "denied"])
async def test_results_and_ids_reach_next_model_turn(tmp_path, status):
    provider = ScriptedProvider([
        ModelTurn("Inspecting", [ToolCall("call-1", "test__read", {})]),
        ModelTurn("Finished"),
    ])
    outcome = await Agent(provider, Dispatcher(status)).run("read", tmp_path)
    assert outcome.status == "completed"
    followup = provider.seen[1]
    assert followup[-2]["tool_calls"][0]["id"] == "call-1"
    assert followup[-1]["tool_call_id"] == "call-1"
    assert json.loads(followup[-1]["content"])["status"] == status
    assert "observed file contents" in followup[-1]["content"]


async def test_limit_does_not_report_success(tmp_path):
    provider = ScriptedProvider([ModelTurn("", [ToolCall("call-1", "test__read", {})])])
    outcome = await Agent(provider, Dispatcher(), max_turns=1).run("read", tmp_path)
    assert outcome.status == "limit"
    assert "incomplete" in outcome.message
    assert len(outcome.tool_results) == 1


async def test_provider_failure_reports_error_without_request_secrets(tmp_path):
    class BrokenProvider:
        async def complete(self, *args):
            raise Exception("request contains SECRET_KEY")
    outcome = await Agent(BrokenProvider(), Dispatcher()).run("read", tmp_path)
    assert outcome.status == "error"
    assert "SECRET_KEY" not in outcome.message


async def test_cancellation_propagates_to_cli(tmp_path):
    class CancelledProvider:
        async def complete(self, *args):
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await Agent(CancelledProvider(), Dispatcher()).run("read", tmp_path)


async def test_empty_model_turn_is_not_completion(tmp_path):
    provider = ScriptedProvider([ModelTurn("")])
    outcome = await Agent(provider, Dispatcher()).run("read", tmp_path)
    assert outcome.status == "error"


async def test_secret_observation_is_redacted_before_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "secret-fixture-value")
    class SecretDispatcher(Dispatcher):
        async def execute(self, call):
            return ToolResult(call.id, call.name, "secret-fixture-value")
    provider = ScriptedProvider([ModelTurn("", [ToolCall("call-1", "test__read", {})]), ModelTurn("Done")])
    await Agent(provider, SecretDispatcher()).run("read", tmp_path)
    assert "secret-fixture-value" not in json.dumps(provider.seen[1])
    assert "[redacted]" in json.dumps(provider.seen[1])
