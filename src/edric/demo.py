"""Repeatable, explicitly predetermined live MCP integration demonstration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol

from .contracts import EventCallback, MCPResult, ToolDefinition

MARKER = ".edric-checkin.json"
MARKER_CONTENT = {"kind": "edric-generated-checkin-workspace", "version": 1}
GENERATED_OUTPUTS = ("documentation.md", "demo-evidence.json")
STARTER_CODE = '''"""Small HTTPX example for the October 8 check-in."""

import httpx


def fetch_json(url: str):
    response = httpx.get(url)
    return response.json()
'''
EXPECTED_CODE = '''"""Small HTTPX example for the October 8 check-in."""

import httpx


def fetch_json(url: str, *, timeout: float = 5.0):
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()
'''
FIXTURE_TESTS = '''"""Offline behavioral checks; no HTTP requests leave this process."""

import unittest
from unittest.mock import patch

import httpx

from client import fetch_json


class FetchJsonTests(unittest.TestCase):
    @staticmethod
    def response(status, body):
        return httpx.Response(
            status,
            json=body,
            request=httpx.Request("GET", "https://example.test/data"),
        )

    @patch("client.httpx.get")
    def test_json_and_default_timeout(self, get):
        get.return_value = self.response(200, {"ok": True})
        self.assertEqual(fetch_json("https://example.test/data"), {"ok": True})
        get.assert_called_once_with("https://example.test/data", timeout=5.0)

    @patch("client.httpx.get")
    def test_timeout_can_be_overridden(self, get):
        get.return_value = self.response(200, {"ok": True})
        fetch_json("https://example.test/data", timeout=2.0)
        get.assert_called_once_with("https://example.test/data", timeout=2.0)

    @patch("client.httpx.get")
    def test_http_errors_are_not_returned_as_success(self, get):
        get.return_value = self.response(500, {"error": "unavailable"})
        with self.assertRaises(httpx.HTTPStatusError):
            fetch_json("https://example.test/data")

    @patch("client.httpx.get")
    def test_network_timeout_propagates(self, get):
        get.side_effect = httpx.TimeoutException("timed out")
        with self.assertRaises(httpx.TimeoutException):
            fetch_json("https://example.test/data")


if __name__ == "__main__":
    unittest.main()
'''
AUTONOMOUS_TASK = (
    "Read client.py and test_client.py in the workspace. Use the external MCP "
    "documentation tools to retrieve HTTPX documentation on request timeouts and "
    "raise_for_status. Update fetch_json to accept a keyword-only timeout defaulting "
    "to 5.0 seconds, pass that timeout to httpx.get, call raise_for_status before "
    "decoding JSON, and preserve HTTPX exceptions. Run the offline unittest suite "
    "with the provided Python interpreter. Do not change the tests. Summarize the "
    "documentation consulted, files changed, and the actual test result."
)


class DemoHub(Protocol):
    tools: dict[str, ToolDefinition]
    server_status: dict[str, str]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResult: ...


def _assert_regular(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing to operate on a symlink in the demo workspace: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"Expected a regular generated demo file: {path}")


def verify_demo_workspace(workspace: Path) -> Path:
    """Require our marker before any demo writes; never adopt arbitrary folders."""
    workspace = Path(workspace).expanduser()
    if workspace.is_symlink():
        raise ValueError("The demo workspace itself must not be a symlink.")
    workspace = workspace.resolve()
    marker = workspace / MARKER
    _assert_regular(marker)
    try:
        found = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("This is not a generated Edric demo workspace. Run 'edric demo prepare'.") from exc
    if found != MARKER_CONTENT:
        raise ValueError("The workspace marker does not identify an Edric check-in fixture.")
    for name in ("client.py", "test_client.py", *GENERATED_OUTPUTS):
        _assert_regular(workspace / name)
    return workspace


def prepare_demo_workspace(workspace: Path, reset: bool = False) -> Path:
    """Create fixture files, or explicitly reset only known generated files.

    Unknown files are preserved even on reset. A nonempty unmarked directory is
    rejected. No directory tree is deleted.
    """
    workspace = Path(workspace).expanduser()
    if workspace.is_symlink():
        raise ValueError("The demo workspace itself must not be a symlink.")
    workspace = workspace.resolve()
    if workspace.exists() and not workspace.is_dir():
        raise ValueError("The demo workspace must be a directory.")
    if workspace.exists() and any(workspace.iterdir()):
        verify_demo_workspace(workspace)
        if not reset:
            return workspace
    else:
        workspace.mkdir(parents=True, exist_ok=True)
    for name in (MARKER, "client.py", "test_client.py", *GENERATED_OUTPUTS):
        _assert_regular(workspace / name)
    (workspace / MARKER).write_text(json.dumps(MARKER_CONTENT, indent=2) + "\n", encoding="utf-8")
    (workspace / "client.py").write_text(STARTER_CODE, encoding="utf-8")
    (workspace / "test_client.py").write_text(FIXTURE_TESTS, encoding="utf-8")
    for name in GENERATED_OUTPUTS:
        (workspace / name).unlink(missing_ok=True)
    return workspace


def _find_tool(hub: DemoHub, names: tuple[str, ...], server: str | None = None) -> ToolDefinition | None:
    for original in names:
        matches = [
            definition for definition in hub.tools.values()
            if (definition.original_name or definition.name.split("__", 1)[-1]) == original
            and (server is None or definition.server == server)
        ]
        if len(matches) > 1:
            raise RuntimeError(f"Ambiguous discovered tool '{original}'; choose a unique server configuration.")
        if matches:
            return matches[0]
    return None


def _arguments(tool: ToolDefinition, values: dict[str, Any]) -> dict[str, Any]:
    """Send fields accepted by the discovered schema, and reject unknown requirements."""
    properties = tool.input_schema.get("properties", {})
    arguments = {key: value for key, value in values.items() if key in properties}
    missing = set(tool.input_schema.get("required", [])) - arguments.keys()
    if missing:
        raise RuntimeError(f"Unsupported required fields for {tool.name}: {', '.join(sorted(missing))}")
    return arguments


def _library_id(content: str) -> str:
    patterns = (
        r"Context7-compatible library ID\s*:\s*[`\"']?(/[^\s`\"',]+)",
        r"(?:Library ID|libraryId)\s*[:=]\s*[`\"']?(/[^\s`\"',]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return match.group(1)
    try:
        parsed = json.loads(content)
    except ValueError:
        parsed = None

    def find(value: Any) -> str | None:
        if isinstance(value, dict):
            for key in ("libraryId", "id", "library_id"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.startswith("/"):
                    return candidate
            for candidate in value.values():
                found = find(candidate)
                if found:
                    return found
        if isinstance(value, list):
            for candidate in value:
                found = find(candidate)
                if found:
                    return found
        return None

    found = find(parsed)
    if found:
        return found
    raise RuntimeError("Context7 returned no identifiable library ID. Inspect its result before retrying.")


async def run_checkin_demo(
    hub: DemoHub,
    workspace: Path,
    on_event: EventCallback | None = None,
) -> dict[str, Any]:
    """Invoke real MCP tools in a known sequence; this does not use a model."""
    workspace = verify_demo_workspace(workspace)
    calls: list[dict[str, Any]] = []

    def emit(name: str, data: dict[str, Any]) -> None:
        if on_event:
            on_event(name, data)

    async def call(tool: ToolDefinition, values: dict[str, Any], purpose: str) -> str:
        arguments = _arguments(tool, values)
        emit("demo_step", {"message": purpose})
        emit("tool_requested", {"tool": tool.name, "server": tool.server, "arguments": arguments})
        emit("tool_running", {"tool": tool.name, "server": tool.server, "arguments": arguments})
        result = await hub.call_tool(tool.name, arguments)
        if result.is_error:
            emit("tool_failed", {"tool": tool.name, "message": result.content})
            raise RuntimeError(f"{tool.name} failed: {result.content}")
        if not result.content.strip():
            raise RuntimeError(f"{tool.name} returned an empty result.")
        calls.append({"tool": tool.name, "server": tool.server, "purpose": purpose})
        emit("tool_finished", {"tool": tool.name, "status": "success", "content": result.content})
        return result.content

    read = _find_tool(hub, ("read_text_file", "read_file"))
    write = _find_tool(hub, ("write_file",), read.server if read else None)
    edit = _find_tool(hub, ("edit_file",), read.server if read else None)
    if not (read and write and edit):
        raise RuntimeError("The demo requires filesystem MCP read, write, and edit tools. Check server connections.")
    resolve = _find_tool(hub, ("resolve-library-id", "resolve_library_id"))
    query = _find_tool(hub, ("query-docs", "query_docs", "get-library-docs", "get_library_docs"), resolve.server if resolve else None)
    fetch = _find_tool(hub, ("fetch",))
    if not ((resolve and query) or fetch):
        raise RuntimeError("No external MCP documentation tools are connected. Configure Context7 or Fetch.")

    emit("demo_step", {"message": "Predetermined live MCP integration demonstration; no LLM is used."})
    listing = _find_tool(hub, ("list_directory",), read.server)
    if listing:
        await call(listing, {"path": str(workspace)}, "List the generated workspace through filesystem MCP")
    client_path = str(workspace / "client.py")
    initial = await call(read, {"path": client_path}, "Read the starter Python file through filesystem MCP")
    if initial != STARTER_CODE:
        raise RuntimeError("The fixture has changed. Repeat with 'edric demo checkin --reset' to restore generated files.")

    question = "HTTPX Python: configure a request timeout and use response.raise_for_status before response.json"
    if resolve and query:
        if resolve.server == read.server:
            raise RuntimeError("The external documentation and filesystem tools must come from two distinct MCP servers.")
        resolved = await call(resolve, {"libraryName": "httpx", "library_name": "httpx", "query": question}, "Resolve HTTPX using external Context7 MCP")
        library = _library_id(resolved)
        docs = await call(query, {
            "libraryId": library,
            "library_id": library,
            "context7CompatibleLibraryID": library,
            "query": question,
            "topic": "timeouts and raise_for_status",
            "tokens": 1500,
            "mode": "code",
        }, "Retrieve HTTPX timeout and error-handling documentation through Context7 MCP")
        external_server = resolve.server
        source = f"Context7 library {library}"
    else:
        assert fetch is not None
        if fetch.server == read.server:
            raise RuntimeError("The external documentation and filesystem tools must come from two distinct MCP servers.")
        url = "https://www.python-httpx.org/quickstart/"
        docs = await call(fetch, {"url": url, "max_length": 8000, "start_index": 0, "raw": False}, "Retrieve HTTPX documentation through external Fetch MCP (configured fallback)")
        external_server = fetch.server
        source = url
    lowered = docs.lower()
    if any(phrase in lowered[:1000] for phrase in ("invalid api key", "unauthorized", "rate limit exceeded", "quota exceeded", "no documentation found", "failed to fetch")):
        raise RuntimeError(f"External server returned an access/retrieval failure: {docs[:500]}")

    artifact = f"# Live MCP documentation retrieval\n\nSource: {source}\n\n{docs}\n"
    await call(write, {"path": str(workspace / "documentation.md"), "content": artifact}, "Write the live documentation result through filesystem MCP")
    await call(edit, {
        "path": client_path,
        "edits": [{"oldText": STARTER_CODE, "newText": EXPECTED_CODE}],
        "dryRun": False,
    }, "Apply the predetermined timeout and HTTP-error handling edit through filesystem MCP")
    changed = await call(read, {"path": client_path}, "Read back and verify the edited file through filesystem MCP")
    if changed != EXPECTED_CODE:
        raise RuntimeError("Filesystem MCP readback did not exactly match the expected edit.")
    report = {
        "kind": "predetermined-live-mcp-integration",
        "verified": True,
        "filesystem_server": read.server,
        "external_server": external_server,
        "workspace": str(workspace),
        "external_source": source,
        "documentation_characters": len(docs),
        "file_readback_matches": True,
        "fixture_tests_run": False,
        "calls": calls,
    }
    # Serialize before the final call so the evidence does not recursively include itself.
    await call(write, {"path": str(workspace / "demo-evidence.json"), "content": json.dumps(report, indent=2) + "\n"}, "Save demonstration evidence through filesystem MCP")
    emit("demo_step", {"message": "Two distinct MCP servers returned live results; edited file verified. Offline fixture tests are a separate check."})
    return report
