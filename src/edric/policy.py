"""Execution decisions are separate from terminal interaction and tool routing."""

from .contracts import ToolDefinition


class ExecutionPolicy:
    def __init__(self, mode: str = "confirm"):
        if mode not in {"confirm", "auto"}:
            raise ValueError("Execution mode must be confirm or auto.")
        self.mode = mode

    def requires_approval(self, tool: ToolDefinition) -> bool:
        return self.mode == "confirm" and tool.mutating is not False
