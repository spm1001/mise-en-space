"""Run async code from sync code, safely on any thread.

Bare asyncio.run() raises RuntimeError on a thread that already runs an event
loop. On mcp 1.x every sync tool body was such a thread, and the two fail-open
adapters that called it swallowed the error — thread-a browser resolution was
dead through the live envelope from birth (mise-wamoco). mcp 2.x moved tool
bodies to worker threads, which hides the hazard without removing it: any
future path that runs on a loop thread (an async tool, a resource handler, a
lifespan task) re-arms it silently.

So this module is the single door: `asyncio.run(` may not appear anywhere
else in shipped code, enforced by tests/unit/test_async_bridge.py
(mise-pagigo).
"""

import asyncio
import concurrent.futures
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def run_async_blocking(coro: Coroutine[Any, Any, T]) -> T:
    """Run `coro` to completion and return its result, from sync code.

    No event loop on this thread: plain asyncio.run(). A loop already
    running here: hand the coroutine to a disposable worker thread, whose
    fresh asyncio.run() is legal, and block on the result — same semantics
    the caller expected, minus the RuntimeError.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
