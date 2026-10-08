import asyncio
import io
import os
import sys

from rich.console import Console

from edric.display import Display
from edric.terminal_input import read_line


def test_stream_redacts_secret_split_across_chunks(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "a-secret-key")
    stream = io.StringIO()
    display = Display(Console(file=stream, force_terminal=False))
    display.stream("Value: a-sec")
    display.stream("ret-key done")
    display.end_stream()
    assert "a-secret-key" not in stream.getvalue()
    assert "Value: [redacted] done" in stream.getvalue()


async def test_cancelled_input_does_not_leave_blocked_thread(monkeypatch):
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "r") as pipe:
        monkeypatch.setattr(sys, "stdin", pipe)
        task = asyncio.create_task(read_line(""))
        await asyncio.sleep(0)
        task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
    os.close(write_fd)
