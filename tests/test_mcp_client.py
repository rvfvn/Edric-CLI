import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import anyio
import pytest
from mcp.types import Tool, ToolAnnotations

from edric import mcp_client
from edric.contracts import ServerConfig
from edric.mcp_client import MCPHub, public_tool_name, render_result


@pytest.fixture
def fake_sdk(monkeypatch):
    state = SimpleNamespace(
        calls=[], lifetimes=[], pages={}, fail=set(), delayed=set(),
        background_failure=asyncio.Event(),
    )

    @asynccontextmanager
    async def stdio(parameters):
        owner = asyncio.current_task()
        state.lifetimes.append((parameters.command, "enter", owner))
        try:
            if parameters.command == "background-failure":
                async def fail_later():
                    await state.background_failure.wait()
                    raise RuntimeError("transport failed with PRIVATE_API_KEY")
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(fail_later)
                    yield parameters.command, None
                    tasks.cancel_scope.cancel()
            else:
                yield parameters.command, None
        finally:
            state.lifetimes.append((parameters.command, "exit", asyncio.current_task()))

    class Session:
        def __init__(self, command, write, **kwargs):
            self.command = command

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            if self.command in state.fail:
                raise RuntimeError("PRIVATE_API_KEY")

        async def list_tools(self, cursor=None):
            pages = state.pages.get(self.command, [[Tool(name="read_file", inputSchema={"type": "object"})]])
            index = int(cursor) if cursor else 0
            return SimpleNamespace(tools=pages[index], nextCursor=str(index + 1) if index + 1 < len(pages) else None)

        async def call_tool(self, name, arguments):
            state.calls.append((self.command, name, arguments))
            if self.command in state.delayed:
                await asyncio.sleep(1)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="real result")], isError=False)

    monkeypatch.setattr(mcp_client, "stdio_client", stdio)
    monkeypatch.setattr(mcp_client, "ClientSession", Session)
    return state


async def test_optional_failure_keeps_working_server_and_hides_credentials(fake_sdk):
    fake_sdk.fail.add("bad")
    events = []
    hub = MCPHub([
        ServerConfig("filesystem", "stdio", command="good"),
        ServerConfig("context7", "stdio", command="bad", required=False),
    ], on_event=lambda event, data: events.append((event, data)))
    async with hub:
        assert hub.server_status["filesystem"] == "connected"
        assert hub.server_status["context7"].startswith("unavailable:")
        result = await hub.call_tool("filesystem__read_file", {"path": "example.py"})
        assert result.content == "real result"
        assert not result.is_error
        assert fake_sdk.calls == [("good", "read_file", {"path": "example.py"})]
    assert "PRIVATE_API_KEY" not in str(events)
    for command in ("good", "bad"):
        lifetime = [record for record in fake_sdk.lifetimes if record[0] == command]
        assert lifetime[0][2] is lifetime[-1][2]


async def test_discovery_pagination_policy_and_atomic_collision_failure(fake_sdk):
    fake_sdk.pages["paged"] = [
        [Tool(name="read_text_file", inputSchema={"type": "object"})],
        [Tool(name="write_file", inputSchema={"type": "object"}),
         Tool(name="new_tool", inputSchema={"type": "object"}),
         Tool(name="annotated_read", inputSchema={}, annotations=ToolAnnotations(readOnlyHint=True))],
    ]
    fake_sdk.pages["collision"] = [[Tool(name="a.b", inputSchema={}), Tool(name="a_b", inputSchema={})]]
    async with MCPHub([
        ServerConfig("filesystem", "stdio", command="paged"),
        ServerConfig("collision", "stdio", command="collision"),
    ]) as hub:
        assert hub.tools["filesystem__read_text_file"].mutating is False
        assert hub.tools["filesystem__write_file"].mutating is True
        assert hub.tools["filesystem__new_tool"].mutating is None
        assert hub.tools["filesystem__annotated_read"].mutating is False
        assert len(hub.tools) == 4
        assert "collision" in hub.server_status["collision"]


async def test_background_transport_failure_does_not_cancel_other_server(fake_sdk):
    async with MCPHub([
        ServerConfig("filesystem", "stdio", command="good"),
        ServerConfig("optional", "stdio", command="background-failure", required=False),
    ]) as hub:
        fake_sdk.background_failure.set()
        for _ in range(100):
            if hub.server_status["optional"].startswith("unavailable:"):
                break
            await asyncio.sleep(0.001)
        assert hub.server_status["optional"].startswith("unavailable:")
        assert "optional__read_file" not in hub.tools
        assert not (await hub.call_tool("filesystem__read_file", {})).is_error


async def test_call_timeout_unknown_tool_and_cancellation(fake_sdk):
    fake_sdk.delayed.add("slow")
    async with MCPHub([ServerConfig("filesystem", "stdio", command="slow")], timeout=0.05) as hub:
        result = await hub.call_tool("filesystem__read_file", {})
        assert result.is_error and "timed out" in result.content
        assert (await hub.call_tool("unknown", {})).is_error
        pending = asyncio.create_task(hub.call_tool("filesystem__read_file", {}))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending


def test_result_preserves_errors_and_structured_data_without_binary():
    result = render_result(SimpleNamespace(
        isError=True,
        content=[SimpleNamespace(type="text", text="problem"),
                 SimpleNamespace(type="image", mimeType="image/png", data="HUGE_BASE64")],
        structuredContent={"reason": "not found"},
    ))
    assert result.is_error
    assert "problem" in result.content
    assert '"reason": "not found"' in result.content
    assert "image/png" in result.content
    assert "HUGE_BASE64" not in result.content


def test_names_fit_provider_limits_and_duplicate_servers_rejected():
    name = public_tool_name("server with spaces", "tool/with.dot")
    assert name == "server_with_spaces__tool_with_dot"
    assert len(public_tool_name("server", "x" * 200)) == 64
    assert public_tool_name("server", "x" * 200) != public_tool_name("server", "x" * 199 + "y")
    with pytest.raises(ValueError, match="unique"):
        MCPHub([ServerConfig("same", "stdio"), ServerConfig("same", "http")])


@pytest.mark.parametrize("text", ["source code\n", ""])
def test_filesystem_structured_wrapper_does_not_change_exact_text(text):
    result = render_result(SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        structuredContent={"content": text}, isError=False,
    ))
    assert result.content == text
