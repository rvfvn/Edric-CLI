"""Tests use a fake hub; only explicit live CLI runs prove MCP integration."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from edric.contracts import MCPResult, ToolDefinition
from edric.demo import EXPECTED_CODE, STARTER_CODE, prepare_demo_workspace, run_checkin_demo


def definition(server, original, properties, required=()):
    return ToolDefinition(
        name=f"{server}__{original.replace('-', '_')}",
        description="Fake test tool",
        input_schema={"type": "object", "properties": {key: {} for key in properties}, "required": list(required)},
        server=server,
        original_name=original,
    )


class FakeHub:
    def __init__(self, external="context7", legacy=False):
        definitions = [
            definition("filesystem", "list_directory", ("path",), ("path",)),
            definition("filesystem", "read_text_file", ("path",), ("path",)),
            definition("filesystem", "write_file", ("path", "content"), ("path", "content")),
            definition("filesystem", "edit_file", ("path", "edits", "dryRun"), ("path", "edits")),
        ]
        if external == "context7":
            definitions.append(definition("context7", "resolve-library-id", ("libraryName", "query"), ("libraryName", "query")))
            if legacy:
                definitions.append(definition("context7", "get-library-docs", ("context7CompatibleLibraryID", "topic", "tokens"), ("context7CompatibleLibraryID",)))
            else:
                definitions.append(definition("context7", "query-docs", ("libraryId", "query"), ("libraryId", "query")))
        if external == "fetch":
            definitions.append(definition("fetch", "fetch", ("url", "max_length", "start_index", "raw"), ("url",)))
        self.tools = {tool.name: tool for tool in definitions}
        self.server_status = {"filesystem": "connected", external: "connected"}
        self.calls = []
        self.external_result = MCPResult("HTTPX documentation: get(timeout=5.0), response.raise_for_status().")
        self.corrupt_readback = False
        self.reads = 0

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        tool = self.tools[name]
        if tool.original_name == "list_directory":
            return MCPResult("client.py\ntest_client.py")
        if tool.original_name == "read_text_file":
            self.reads += 1
            if self.corrupt_readback and self.reads > 1:
                return MCPResult("not the actual expected contents")
            return MCPResult(Path(arguments["path"]).read_text())
        if tool.original_name == "write_file":
            Path(arguments["path"]).write_text(arguments["content"])
            return MCPResult("Successfully wrote file")
        if tool.original_name == "edit_file":
            path = Path(arguments["path"])
            content = path.read_text()
            for edit in arguments["edits"]:
                content = content.replace(edit["oldText"], edit["newText"])
            path.write_text(content)
            return MCPResult("Edit applied")
        if tool.original_name == "resolve-library-id":
            return MCPResult("Title: HTTPX\n- Context7-compatible library ID: /encode/httpx\n")
        return self.external_result


def test_preparation_rejects_existing_unmarked_folder(tmp_path):
    (tmp_path / "valuable.txt").write_text("keep")
    with pytest.raises(ValueError, match="not a generated"):
        prepare_demo_workspace(tmp_path, reset=True)
    assert (tmp_path / "valuable.txt").read_text() == "keep"


def test_reset_only_replaces_generated_files(tmp_path):
    workspace = prepare_demo_workspace(tmp_path / "demo")
    (workspace / "client.py").write_text("changed")
    (workspace / "notes.txt").write_text("keep")
    (workspace / "documentation.md").write_text("old docs")
    prepare_demo_workspace(workspace, reset=True)
    assert (workspace / "client.py").read_text() == STARTER_CODE
    assert (workspace / "notes.txt").read_text() == "keep"
    assert not (workspace / "documentation.md").exists()


def test_preparation_without_reset_preserves_changes(tmp_path):
    workspace = prepare_demo_workspace(tmp_path / "demo")
    (workspace / "client.py").write_text("changed")
    prepare_demo_workspace(workspace)
    assert (workspace / "client.py").read_text() == "changed"


def test_reset_rejects_generated_symlinks(tmp_path):
    workspace = prepare_demo_workspace(tmp_path / "demo")
    valuable = tmp_path / "valuable.py"
    valuable.write_text("keep")
    (workspace / "client.py").unlink()
    (workspace / "client.py").symlink_to(valuable)
    with pytest.raises(ValueError, match="symlink"):
        prepare_demo_workspace(workspace, reset=True)
    assert valuable.read_text() == "keep"


@pytest.mark.parametrize("external,legacy", [("context7", False), ("context7", True), ("fetch", False)])
def test_demo_uses_discovered_schemas_and_verifies_readback(tmp_path, external, legacy):
    workspace = prepare_demo_workspace(tmp_path / "demo")
    hub = FakeHub(external=external, legacy=legacy)
    report = asyncio.run(run_checkin_demo(hub, workspace))
    assert report["kind"] == "predetermined-live-mcp-integration"
    assert report["verified"] is True
    assert report["fixture_tests_run"] is False
    assert report["external_server"] == external
    assert report["external_server"] != report["filesystem_server"]
    assert (workspace / "client.py").read_text() == EXPECTED_CODE
    assert (workspace / "documentation.md").exists()
    evidence = json.loads((workspace / "demo-evidence.json").read_text())
    assert evidence["file_readback_matches"] is True
    for name, arguments in hub.calls:
        schema = hub.tools[name].input_schema
        assert set(arguments) <= set(schema["properties"])
        assert set(schema["required"]) <= set(arguments)


def test_external_failure_stops_before_edit(tmp_path):
    workspace = prepare_demo_workspace(tmp_path / "demo")
    hub = FakeHub()
    hub.external_result = MCPResult("Invalid API key", is_error=True)
    with pytest.raises(RuntimeError, match="Invalid API key"):
        asyncio.run(run_checkin_demo(hub, workspace))
    assert (workspace / "client.py").read_text() == STARTER_CODE
    assert not (workspace / "demo-evidence.json").exists()


def test_plaintext_access_failure_is_not_success(tmp_path):
    workspace = prepare_demo_workspace(tmp_path / "demo")
    hub = FakeHub()
    hub.external_result = MCPResult("Rate limit exceeded")
    with pytest.raises(RuntimeError, match="access/retrieval failure"):
        asyncio.run(run_checkin_demo(hub, workspace))
    assert (workspace / "client.py").read_text() == STARTER_CODE


def test_readback_mismatch_never_creates_success_evidence(tmp_path):
    workspace = prepare_demo_workspace(tmp_path / "demo")
    hub = FakeHub()
    hub.corrupt_readback = True
    with pytest.raises(RuntimeError, match="readback"):
        asyncio.run(run_checkin_demo(hub, workspace))
    assert not (workspace / "demo-evidence.json").exists()


def test_missing_external_server_rejected_before_writes(tmp_path):
    workspace = prepare_demo_workspace(tmp_path / "demo")
    hub = FakeHub(external="none")
    with pytest.raises(RuntimeError, match="No external MCP"):
        asyncio.run(run_checkin_demo(hub, workspace))
    assert not hub.calls


def test_fixture_detects_starter_bug_then_passes_expected_behavior(tmp_path):
    workspace = prepare_demo_workspace(tmp_path / "demo")
    command = [sys.executable, "-m", "unittest", "discover", "-v"]
    before = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=30)
    assert before.returncode != 0
    assert "FAILED" in before.stderr
    (workspace / "client.py").write_text(EXPECTED_CODE)
    after = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=30)
    assert after.returncode == 0, after.stderr
    assert "Ran 4 tests" in after.stderr
