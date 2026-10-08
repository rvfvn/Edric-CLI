"""Validate requested calls, apply execution policy, and return model observations."""

from collections.abc import Awaitable, Callable
from pathlib import Path

from jsonschema import Draft202012Validator

from .commands import COMMAND_TOOL, run_command
from .contracts import EventCallback, ToolCall, ToolDefinition, ToolResult
from .policy import ExecutionPolicy
from .secrets import redact

Approval = Callable[[ToolDefinition, ToolCall], Awaitable[bool]]


class ToolDispatcher:
    def __init__(self, hub, workspace: Path, policy: ExecutionPolicy,
                 approval: Approval | None = None, on_event: EventCallback | None = None):
        self.hub = hub
        self.workspace = workspace.resolve()
        self.policy = policy
        self.approval = approval
        self.on_event = on_event
        self.tools = dict(hub.tools)
        if COMMAND_TOOL.name in self.tools:
            raise ValueError("A discovered tool conflicts with the built-in command runner.")
        self.tools[COMMAND_TOOL.name] = COMMAND_TOOL

    def event(self, name, **data):
        if self.on_event:
            self.on_event(name, data)

    async def execute(self, call: ToolCall) -> ToolResult:
        tool = self.tools.get(call.name)
        self.event("tool_requested", tool=call.name, arguments=call.arguments,
                   server=tool.server if tool else "unknown")
        if tool is None:
            return self._result(call, "Unknown tool. Choose a tool from the available inventory.", "error")
        if not isinstance(call.arguments, dict):
            return self._result(call, "Tool arguments must be a JSON object.", "error")
        try:
            errors = sorted(Draft202012Validator(tool.input_schema).iter_errors(call.arguments),
                            key=lambda e: str(list(e.path)))
        except Exception:
            return self._result(call, "Tool has an invalid argument schema.", "error")
        if errors:
            # Do not include supplied values (which could contain secrets) in error text.
            locations = [".".join(map(str, error.path)) or "arguments" for error in errors]
            return self._result(call, "Invalid arguments at: " + ", ".join(locations), "error")
        if tool.server == "filesystem":
            paths = []
            for key in ("path", "source", "destination"):
                if isinstance(call.arguments.get(key), str):
                    paths.append(call.arguments[key])
            paths.extend(path for path in call.arguments.get("paths", []) if isinstance(path, str))
            for value in paths:
                path = Path(value).expanduser()
                path = (self.workspace / path if not path.is_absolute() else path).resolve()
                if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
                    return self._result(call, "Local credential files are excluded from agent file operations.", "denied")
        if self.policy.requires_approval(tool):
            self.event("approval_needed", tool=call.name, server=tool.server)
            if self.approval is None or not await self.approval(tool, call):
                return self._result(call, "User denied this action. It was not executed.", "denied")
        self.event("tool_running", tool=call.name, server=tool.server)
        try:
            if call.name == COMMAND_TOOL.name:
                content, is_error = await run_command(call.arguments, self.workspace)
            else:
                result = await self.hub.call_tool(call.name, call.arguments)
                content, is_error = result.content, result.is_error
            return self._result(call, content, "error" if is_error else "success")
        except Exception as exc:
            return self._result(call, f"Tool execution failed ({type(exc).__name__}).", "error")

    def _result(self, call, content, status):
        content = redact(content)
        result = ToolResult(call.id, call.name, content, status)
        self.event("tool_finished", tool=call.name, status=status, content=content)
        return result
