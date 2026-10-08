"""Readable terminal evidence, with local credential values removed from output."""

import json

from rich.console import Console
from rich.table import Table
from .secrets import redact, secret_values


class Display:
    def __init__(self, console: Console | None = None, verbose: bool = False):
        self.console = console or Console()
        self.verbose = verbose
        self.streaming = False
        self._stream_pending = ""

    def text(self, content: str):
        self.console.print(redact(str(content)), markup=False)

    def stream(self, fragment: str):
        combined = redact(self._stream_pending + fragment)
        keep = 0
        for secret in secret_values():
            for length in range(1, min(len(secret), len(combined)) + 1):
                if combined.endswith(secret[:length]):
                    keep = max(keep, length)
        safe = combined[:-keep] if keep else combined
        self._stream_pending = combined[-keep:] if keep else ""
        self.console.print(safe, end="", markup=False, highlight=False)
        self.streaming = True

    def end_stream(self):
        if self.streaming:
            if self._stream_pending:
                self.console.print("[redacted]", end="", markup=False)
                self._stream_pending = ""
            self.console.print()
            self.streaming = False

    def event(self, name: str, data: dict):
        self.end_stream()
        if name == "model_turn":
            self.console.print(f"Model turn {data['turn']}/{data['max_turns']}", style="dim")
        elif name == "server_connecting":
            self.text(f"Connecting: {data.get('server', data.get('name', '?'))}")
        elif name == "server_connected":
            self.text(f"Connected: {data.get('server', data.get('name', '?'))} ({data.get('tools', data.get('tool_count', '?'))} tools)")
        elif name == "server_failed":
            self.text(f"Unavailable: {data.get('server', data.get('name', '?'))} — {data.get('error', data.get('reason', 'connection failed'))}")
        elif name == "tool_requested":
            self.console.rule(redact(str(data.get("tool", "Tool"))), style="cyan")
            arguments = json.dumps(data.get("arguments", {}), ensure_ascii=False)
            self.text(arguments if self.verbose else arguments[:1400])
        elif name == "approval_needed":
            self.text("Status: awaiting approval")
        elif name == "tool_running":
            self.text(f"Status: running ({data.get('server', 'tool')})")
        elif name == "tool_finished":
            self.text(f"Status: {data.get('status', '?')}")
            content = str(data.get("content", ""))
            self.text(content if self.verbose else content[:3000])
            if not self.verbose and len(content) > 3000:
                self.text("[Display preview truncated; use --verbose for full tool output.]")
        else:
            # Demo events use the same display pathway, without Rich markup interpretation.
            self.text(f"{name}: {json.dumps(data, ensure_ascii=False, default=str)}")

    def tools(self, definitions):
        table = Table(title="Discovered MCP tools")
        table.add_column("Server")
        table.add_column("Tool")
        table.add_column("Description")
        for tool in definitions:
            table.add_row(tool.server, tool.name, redact(tool.description[:110]))
        self.console.print(table)
