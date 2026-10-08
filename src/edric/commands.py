"""Run workspace commands with bounded capture, timeout, and process cleanup."""

import asyncio
import json
import os
import signal
from pathlib import Path

from .contracts import ToolDefinition
from .secrets import command_environment, redact

COMMAND_TOOL = ToolDefinition(
    name="local__run_command",
    description="Run a shell command or tests in the selected project workspace. Returns exit status, stdout and stderr. Commands require confirmation in confirm mode.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "minLength": 1},
            "timeout": {"type": "number", "minimum": 1, "maximum": 120},
        },
        "required": ["command"], "additionalProperties": False,
    },
    original_name="run_command", mutating=True,
)


async def run_command(arguments: dict, workspace: Path, output_limit: int = 12000) -> tuple[str, bool]:
    """Return serialized observation and an error flag. The cwd is not a sandbox."""
    process = await asyncio.create_subprocess_shell(
        arguments["command"], cwd=workspace,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=command_environment(),
    )
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}

    async def drain(stream, label):
        while chunk := await stream.read(4096):
            remaining = max(0, output_limit - len(captured[label]))
            captured[label].extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated[label] = True

    async def stop():
        # The shell can exit while a child keeps its stdout pipe open. Stop the
        # whole group even when the shell's return code is already available.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), 2)
            except asyncio.TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
        # Remaining children may ignore SIGTERM after the shell has exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    timed_out = False
    readers = [asyncio.create_task(drain(process.stdout, "stdout")),
               asyncio.create_task(drain(process.stderr, "stderr"))]
    try:
        await asyncio.wait_for(asyncio.gather(process.wait(), *readers),
                               float(arguments.get("timeout", 60)))
    except asyncio.TimeoutError:
        timed_out = True
        await stop()
        await asyncio.gather(*readers, return_exceptions=True)
    except asyncio.CancelledError:
        await stop()
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        raise
    result = {
        "command": arguments["command"], "working_directory": str(workspace),
        "exit_code": process.returncode, "timed_out": timed_out,
        "stdout": captured["stdout"].decode(errors="replace"),
        "stderr": captured["stderr"].decode(errors="replace"),
        "output_truncated": any(truncated.values()),
    }
    return redact(json.dumps(result, ensure_ascii=False)), timed_out or process.returncode != 0
