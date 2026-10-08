"""Discover and invoke real MCP tools using the official Python SDK.

Each connection owns its SDK contexts in a dedicated task. AnyIO task groups
must enter and exit in the same task, and an optional HTTP server's failure must
not cancel another server or the terminal interface.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.client.streamable_http import streamable_http_client

from edric.contracts import EventCallback, MCPResult, ServerConfig, ToolDefinition


_FILESYSTEM_READ = {
    "read_file", "read_text_file", "read_media_file", "read_multiple_files",
    "list_directory", "list_directory_with_sizes", "directory_tree",
    "search_files", "get_file_info", "list_allowed_directories",
}
_FILESYSTEM_WRITE = {"write_file", "edit_file", "create_directory", "move_file"}
_EXTERNAL_READ = {"resolve-library-id", "query-docs", "get-library-docs", "fetch"}


class ToolNameCollision(ValueError):
    """Two discovered tools map to the same provider-compatible name."""


def public_tool_name(server: str, tool: str) -> str:
    """Produce a provider-compatible identifier, limited to 64 characters."""
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{server}__{tool}")
    if len(name) > 64:
        suffix = hashlib.sha256(name.encode()).hexdigest()[:10]
        name = f"{name[:53]}_{suffix}"
    return name


def _mutation(server: str, tool: Any) -> bool | None:
    annotations = getattr(tool, "annotations", None)
    if annotations is not None:
        read_only = getattr(annotations, "readOnlyHint", None)
        if read_only is True:
            return False
        if read_only is False or getattr(annotations, "destructiveHint", None) is True:
            return True
    if server == "filesystem":
        if tool.name in _FILESYSTEM_READ:
            return False
        if tool.name in _FILESYSTEM_WRITE:
            return True
    if server in {"context7", "fetch"} and tool.name in _EXTERNAL_READ:
        return False
    return None


def _safe_error(exc: BaseException) -> str:
    """Give useful failure categories without printing exception payloads."""
    if isinstance(exc, BaseExceptionGroup):
        return _safe_error(exc.exceptions[0]) if exc.exceptions else "Connection failed"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "Connection or request timed out"
    if isinstance(exc, FileNotFoundError):
        return "Server executable not found; install the configured server"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return f"Authentication failed (HTTP {status}); check the local API key"
        if status == 429:
            return "Rate limit reached (HTTP 429)"
        return f"HTTP request failed ({status})"
    if isinstance(exc, httpx.ConnectError):
        return "Network connection failed"
    if isinstance(exc, ToolNameCollision):
        return "Discovered tool-name collision; use distinct server/tool names"
    return f"MCP operation failed ({type(exc).__name__})"


def render_result(result: Any) -> MCPResult:
    """Preserve text and structured data; describe binary data without dumping it."""
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        kind = getattr(block, "type", "unknown")
        if kind == "text":
            parts.append(block.text)
        elif kind in {"image", "audio"}:
            parts.append(f"[{kind} content: {getattr(block, 'mimeType', 'unknown MIME type')}]")
        elif kind == "resource":
            resource = block.resource
            text = getattr(resource, "text", None)
            parts.append(text if text is not None else f"[binary resource: {resource.uri}]")
        elif kind == "resource_link":
            parts.append(f"[resource: {getattr(block, 'name', '')} — {getattr(block, 'uri', '')}]")
        else:
            parts.append(f"[{kind} content]")
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        encoded = json.dumps(structured, ensure_ascii=False, default=str)
        # Servers often supply the same JSON in both text and structured form.
        represented = structured == {"content": "\n".join(parts)}
        if not represented:
            try:
                represented = json.loads("\n".join(parts)) == structured
            except (ValueError, TypeError):
                represented = False
        if not represented:
            parts.append(f"Structured result: {encoded}")
    content = "\n".join(parts) if parts else "[empty tool result]"
    return MCPResult(content, bool(getattr(result, "isError", False)))


@dataclass
class _Connection:
    config: ServerConfig
    ready: asyncio.Future[None]
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    session: ClientSession | None = None
    tool_names: list[str] = field(default_factory=list)


class MCPHub:
    """Async lifetime manager and registry for independently configured servers."""

    def __init__(
        self,
        servers: list[ServerConfig],
        on_event: EventCallback | None = None,
        timeout: float = 30.0,
    ) -> None:
        if timeout <= 0:
            raise ValueError("MCP timeout must be positive")
        if len({server.name for server in servers}) != len(servers):
            raise ValueError("MCP server names must be unique")
        self.servers = servers
        self.on_event = on_event
        self.timeout = timeout
        self.tools: dict[str, ToolDefinition] = {}
        self.server_status: dict[str, str] = {}
        self._connections: dict[str, _Connection] = {}
        self._entered = False
        self._closing = False

    def _emit(self, event: str, **data: Any) -> None:
        if self.on_event is not None:
            self.on_event(event, data)

    async def __aenter__(self) -> MCPHub:
        if self._entered:
            raise RuntimeError("MCPHub is already open")
        self._entered = True
        self._closing = False
        self.tools.clear()
        self.server_status.clear()
        try:
            for config in self.servers:
                if not config.enabled:
                    self.server_status[config.name] = "disabled"
                    continue
                connection = _Connection(config, asyncio.get_running_loop().create_future())
                self._connections[config.name] = connection
                self.server_status[config.name] = "connecting"
                self._emit("server_connecting", server=config.name)
                connection.task = asyncio.create_task(self._run_connection(connection), name=f"mcp:{config.name}")
                await connection.ready
            return self
        except BaseException:
            await self.close()
            raise

    async def _run_connection(self, connection: _Connection) -> None:
        config = connection.config
        try:
            async with AsyncExitStack() as stack:
                # asyncio timeout leaves SDK cancel scopes in their owning task.
                async with asyncio.timeout(self.timeout):
                    if config.transport == "stdio":
                        if not config.command:
                            raise ValueError("stdio requires a command")
                        parameters = StdioServerParameters(
                            command=config.command,
                            args=config.args,
                            env={**get_default_environment(), **config.env},
                        )
                        read, write = await stack.enter_async_context(stdio_client(parameters))
                    elif config.transport == "http":
                        if not config.url:
                            raise ValueError("HTTP requires a URL")
                        http_client = await stack.enter_async_context(
                            httpx.AsyncClient(headers=config.headers, timeout=httpx.Timeout(self.timeout))
                        )
                        streams = await stack.enter_async_context(
                            streamable_http_client(config.url, http_client=http_client)
                        )
                        read, write = streams[:2]
                    else:
                        raise ValueError("Unsupported MCP transport")
                    session = await stack.enter_async_context(
                        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=self.timeout))
                    )
                    await session.initialize()
                    discovered: dict[str, ToolDefinition] = {}
                    cursor = None
                    seen_cursors: set[str] = set()
                    while True:
                        page = await session.list_tools(cursor=cursor)
                        for tool in page.tools:
                            name = public_tool_name(config.name, tool.name)
                            if name in self.tools or name in discovered:
                                raise ToolNameCollision()
                            discovered[name] = ToolDefinition(
                                name=name,
                                description=tool.description or f"{config.name}: {tool.name}",
                                input_schema=tool.inputSchema,
                                server=config.name,
                                original_name=tool.name,
                                mutating=_mutation(config.name, tool),
                            )
                        cursor = page.nextCursor
                        if not cursor:
                            break
                        if cursor in seen_cursors:
                            raise ValueError("Server repeated a pagination cursor")
                        seen_cursors.add(cursor)
                    connection.session = session
                    connection.tool_names = list(discovered)
                    self.tools.update(discovered)
                    self.server_status[config.name] = "connected"
                    self._emit("server_connected", server=config.name, tools=len(discovered))
                    if not connection.ready.done():
                        connection.ready.set_result(None)
                await connection.stop.wait()
        except asyncio.CancelledError:
            if not self._closing:
                self._fail_connection(connection, "Server connection cancelled")
            raise
        except Exception as exc:
            self._fail_connection(connection, _safe_error(exc))
        finally:
            connection.session = None
            if not connection.ready.done():
                connection.ready.set_result(None)

    def _fail_connection(self, connection: _Connection, reason: str) -> None:
        for name in connection.tool_names:
            self.tools.pop(name, None)
        connection.session = None
        self.server_status[connection.config.name] = f"unavailable: {reason}"
        if not self._closing:
            self._emit("server_failed", server=connection.config.name, error=reason)

    async def call_tool(self, name: str, arguments: dict) -> MCPResult:
        definition = self.tools.get(name)
        if definition is None:
            return MCPResult("Unknown or unavailable MCP tool", is_error=True)
        connection = self._connections.get(definition.server)
        if connection is None or connection.session is None:
            return MCPResult("MCP server is unavailable", is_error=True)
        try:
            async with asyncio.timeout(self.timeout):
                result = await connection.session.call_tool(definition.original_name, arguments=arguments)
            return render_result(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return MCPResult(_safe_error(exc), is_error=True)

    async def close(self) -> None:
        self._closing = True
        for connection in reversed(list(self._connections.values())):
            connection.stop.set()
            task = connection.task
            if task is None:
                continue
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=self.timeout)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            except asyncio.CancelledError:
                if not task.cancelled():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                # A cancelled worker is expected when its transport fails.
                if asyncio.current_task().cancelling():
                    raise
        self._connections.clear()
        self.tools.clear()
        self._entered = False

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.close()
