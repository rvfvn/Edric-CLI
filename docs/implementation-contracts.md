# Implementation contracts (October 8)

All application contracts live in `src/edric/contracts.py`. Python 3.12; official
MCP Python SDK 1.x initially, pinned after installation and verification. This
choice also supports the Fetch fallback without an SDK major-version conflict.

## MCP client contract

`MCPHub(servers: list[ServerConfig], on_event: EventCallback | None = None,
timeout: float = 30.0)` is an async context manager. Entering connects configured
enabled servers and discovers tools. Public members:

- `tools: dict[str, ToolDefinition]`: public names to discovered definitions.
- `server_status: dict[str, str]`: connection result by server name.
- `async call_tool(name: str, arguments: dict) -> MCPResult`.
- Startup should report unavailable servers and preserve successful connections;
  callers determine which servers are required for their operation.
- Events use `(event_name, data_dict)`; names `server_connecting`,
  `server_connected`, `server_failed`. No credentials in events/errors.
- HTTP uses Streamable HTTP. stdio uses command + args and explicit environment.
- Keep sessions alive, clean up in reverse order. Follow SDK async context lifetimes.
- Namespace and normalize tool names; fail on any collision rather than overwrite.
- Preserve error flags and text/structured results; render non-text content safely.

## Provider contract

`GroqProvider(model: str, api_key: str | None = None, stream: bool = True,
timeout: float = 60.0)` implements `Provider.complete`. Use the official Groq
client's async API. Return `ModelTurn` with a continuation-safe assistant message;
tool calls reference public registry names. JSON argument parsing failures must
be explained without executing anything. `async close()` releases resources.

`create_provider(name, model=None, stream=True)` constructs Groq by default;
Bedrock and Ollama skeletons fail with explicit setup/not-implemented messages.
No automatic paid-provider fallback. Model selection is configurable.

## Demo contract

`async run_checkin_demo(hub, workspace: Path, on_event=None) -> dict` calls real
discovered filesystem tools and Context7 tools (or configured Fetch fallback).
Returns verification results; raises on missing servers, MCP errors, or bad readback.
Predetermined tool calls are explicitly labeled as an integration demonstration.
Fixture/reset operates only on a dedicated generated demo directory.

## Core agent contract

`Agent(provider, dispatcher, on_event=None, max_turns=12)`:
`async run(task: str, workspace: Path, on_text=None) -> AgentOutcome`.
Dispatcher exposes `tools` and `async execute(ToolCall) -> ToolResult`.
Calls are sequential and each result goes back into model conversation, including
failure/denial. Cancellation propagates to the CLI. Limit/failure never reports success.

The CLI owns interaction and presentation. Tests can substitute a scripted provider
and dispatcher; those tests are never represented as live integration evidence.
