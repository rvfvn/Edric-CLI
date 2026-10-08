import asyncio
import json
import shlex
import sys

import pytest

from edric.commands import run_command


async def test_command_cwd_exit_and_capture(tmp_path):
    content, error = await run_command({"command": "pwd; exit 3"}, tmp_path)
    result = json.loads(content)
    assert error
    assert result["exit_code"] == 3
    assert str(tmp_path.resolve()) in result["stdout"]


async def test_timeout_stops_process(tmp_path):
    cmd = shlex.quote(sys.executable) + ' -c "import time; time.sleep(30)"'
    content, error = await run_command({"command": cmd, "timeout": 0.1}, tmp_path)
    assert error
    assert json.loads(content)["timed_out"]


async def test_capture_is_bounded(tmp_path):
    cmd = shlex.quote(sys.executable) + ' -c "print(\'x\' * 5000)"'
    content, error = await run_command({"command": cmd}, tmp_path, output_limit=100)
    result = json.loads(content)
    assert not error
    assert len(result["stdout"]) == 100
    assert result["output_truncated"]


async def test_background_child_with_open_pipe_cannot_hang_forever(tmp_path):
    # A shell exit does not imply its child has closed inherited stdout.
    cmd = "sleep 30 &"
    content, error = await asyncio.wait_for(run_command({"command": cmd, "timeout": 0.1}, tmp_path), 5)
    assert error
    assert json.loads(content)["timed_out"]


async def test_provider_key_is_not_in_child_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "secret-fixture")
    cmd = shlex.quote(sys.executable) + ' -c "import os; print(os.getenv(\'GROQ_API_KEY\'))"'
    content, error = await run_command({"command": cmd}, tmp_path)
    assert not error
    assert json.loads(content)["stdout"].strip() == "None"
