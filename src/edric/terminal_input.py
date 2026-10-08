"""Cancellable POSIX terminal input, without leaving blocked executor threads."""

import asyncio
import sys


async def read_line(prompt: str) -> str:
    print(prompt, end="", flush=True)
    loop = asyncio.get_running_loop()
    try:
        descriptor = sys.stdin.fileno()
    except (AttributeError, ValueError):
        return await asyncio.to_thread(input)
    ready = loop.create_future()

    def on_readable():
        if ready.done():
            return
        try:
            line = sys.stdin.readline()
            if not line:
                ready.set_exception(EOFError())
            else:
                ready.set_result(line.rstrip("\r\n"))
        except Exception as exc:
            ready.set_exception(exc)

    try:
        loop.add_reader(descriptor, on_readable)
    except (NotImplementedError, PermissionError):
        # Windows event loops and regular-file stdin do not support add_reader.
        return await asyncio.to_thread(input)
    try:
        return await ready
    finally:
        loop.remove_reader(descriptor)
