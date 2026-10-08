"""Small shared contracts for the CLI, provider adapters, and MCP integrations."""

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol

JSON = dict[str, Any]
TextCallback = Callable[[str], None]
EventCallback = Callable[[str, JSON], None]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: JSON
    server: str = "local"
    original_name: str = ""
    # None means unknown; confirmation mode asks before executing unknown tools.
    mutating: bool | None = None


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: JSON


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    tool_name: str
    content: str
    status: Literal["success", "error", "denied"] = "success"


@dataclass
class ModelTurn:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Provider-specific assistant message needed for continuation (e.g. tool calls).
    raw_message: JSON | None = None


class Provider(Protocol):
    async def complete(
        self,
        messages: list[JSON],
        tools: list[ToolDefinition],
        on_text: TextCallback | None = None,
    ) -> ModelTurn: ...


@dataclass(frozen=True)
class ServerConfig:
    name: str
    transport: Literal["stdio", "http"]
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    required: bool = True
    enabled: bool = True


@dataclass(frozen=True)
class MCPResult:
    content: str
    is_error: bool = False


@dataclass
class AgentOutcome:
    status: Literal["completed", "limit", "error", "cancelled"]
    message: str
    turns: int
    tool_results: list[ToolResult] = field(default_factory=list)
