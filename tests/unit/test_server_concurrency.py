"""Sync tool handlers stay serialized under mcp 2.x (mise-vucufu step 1).

mcp 2.x runs sync (def) tool handlers on anyio worker threads — 1.x ran them
inline on the event loop, one at a time. server.py's `_serialized` decorator
(a plain threading.Lock) keeps tool bodies one-at-a-time until the
thread-safety audit (mise-vucufu step 2) deletes it.

Why a lock and not an anyio limiter cap: the stdio transport's blocked stdin
read borrows the same default worker pool, so capping it at 1 starves tool
bodies into deadlock (measured 2026-08-23 — the consumer-doors stdio walk hung).

The control test proves the instrument first: two UNdecorated sync bodies on
v2's default pool really do interleave. Without that control, the
serialization test could pass vacuously and we'd never know.
"""

import asyncio
import time

import server


def _make_probe(events: list[tuple[str, int]], name: str):
    def probe(tag: int) -> int:
        events.append(("start", tag))
        time.sleep(0.15)
        events.append(("end", tag))
        return tag

    probe.__name__ = name
    return probe


async def _run_two_calls(name: str, fn) -> None:
    server.mcp.add_tool(fn, name=name)
    try:
        await asyncio.gather(
            server.mcp.call_tool(name, {"tag": 1}),
            server.mcp.call_tool(name, {"tag": 2}),
        )
    finally:
        # Same internal structure resources/tools.py reads; keep the singleton clean.
        server.mcp._tool_manager._tools.pop(name, None)


def _overlapped(events: list[tuple[str, int]]) -> bool:
    kinds = [kind for kind, _ in events]
    return kinds != ["start", "end", "start", "end"]


async def test_control_v2_default_pool_interleaves_sync_tools() -> None:
    """Known-positive control: on v2's default worker pool two undecorated
    sync bodies overlap. If this ever fails, the serialization test below is
    no longer measuring anything — investigate before trusting it."""
    events: list[tuple[str, int]] = []
    await _run_two_calls("_probe_bare", _make_probe(events, "_probe_bare"))
    assert _overlapped(events), (
        f"expected overlap on the default pool, saw {events} — the instrument "
        "can no longer see concurrency; the serialization test is not evidence"
    )


async def test_serialized_decorator_pins_one_at_a_time() -> None:
    """The same probe behind server._serialized never interleaves."""
    events: list[tuple[str, int]] = []
    await _run_two_calls(
        "_probe_locked", server._serialized(_make_probe(events, "_probe_locked"))
    )
    assert not _overlapped(events), (
        f"tool bodies interleaved despite the serializer lock: {events}"
    )


async def test_real_tools_wear_the_lock_and_keep_their_schemas() -> None:
    """The seam, both halves: the three shipped tools actually carry the
    decorator (its absence would silently re-enable concurrency), and
    functools.wraps preserved the signatures the SDK turns into schemas
    (their loss would silently strip every parameter from the tool surface)."""
    for name in ("search", "fetch", "do"):
        assert hasattr(getattr(server, name), "__wrapped__"), (
            f"server.{name} is not decorated with _serialized"
        )
    tools = {t.name: t for t in await server.mcp.list_tools()}
    assert "raw_query" in tools["search"].input_schema["properties"]
    assert "attachment" in tools["fetch"].input_schema["properties"]
    assert "operation" in tools["do"].input_schema["properties"]
