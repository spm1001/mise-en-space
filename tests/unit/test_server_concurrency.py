"""Sync tool handlers run CONCURRENTLY under mcp 2.x (mise-bapije).

mcp 2.x runs sync (def) tool handlers on anyio worker threads — 1.x ran them
inline on the event loop, one at a time. The 1.71.0 migration pinned bodies
one-at-a-time with server.py's `_serialized` threading.Lock while the
thread-safety audit ran; the audit completed 2026-08-24 (mise-bapije) and the
lock is DELETED. Concurrency is now the designed state, guarded where the
shared state actually lives: a per-resource deposit lock in
tools/fetch/router.py, O_EXCL search-deposit naming in workspace/manager.py,
and a single-flight token-refresh lock in adapters/http_client.py — each with
its own test beside the code it guards.

Why the lock's replacement was never an anyio limiter cap: the stdio
transport's blocked stdin read borrows the same default worker pool, so
capping it at 1 starves tool bodies into deadlock (measured 2026-08-23 — the
consumer-doors stdio walk hung). Do not resurrect that idea.

The control test proves the instrument: two sync bodies on v2's default pool
really do interleave. It is the same control that guarded the old pin test —
kept because without it, any future serialization claim would be untestable.
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
        # Public API (mise-vubeku); keep the module-level server singleton clean.
        server.mcp.remove_tool(name)


def _overlapped(events: list[tuple[str, int]]) -> bool:
    kinds = [kind for kind, _ in events]
    return kinds != ["start", "end", "start", "end"]


async def test_control_v2_default_pool_interleaves_sync_tools() -> None:
    """Known-positive control: on v2's default worker pool two undecorated
    sync bodies overlap. If this ever fails, concurrency itself has been
    re-pinned somewhere — investigate before trusting any other result here."""
    events: list[tuple[str, int]] = []
    await _run_two_calls("_probe_bare", _make_probe(events, "_probe_bare"))
    assert _overlapped(events), (
        f"expected overlap on the default pool, saw {events} — either the SDK "
        "changed its dispatch or a serializer has quietly returned"
    )


async def test_shipped_tools_are_undecorated_and_keep_their_schemas() -> None:
    """The seam, both halves inverted from the 1.71.0 era: the three shipped
    tools carry NO wrapper (a __wrapped__ chain reappearing would mean someone
    re-serialized them without reopening the mise-bapije audit), and their
    signatures still reach the SDK's schema generation intact."""
    assert not hasattr(server, "_serialized"), (
        "server._serialized exists again — the mise-bapije deletion was "
        "reverted; reopen the thread-safety audit before shipping"
    )
    for name in ("search", "fetch", "do"):
        assert not hasattr(getattr(server, name), "__wrapped__"), (
            f"server.{name} is wrapped — tool bodies are no longer the raw "
            "functions; if this is a new serializer, reopen mise-bapije"
        )
    tools = {t.name: t for t in await server.mcp.list_tools()}
    assert "raw_query" in tools["search"].input_schema["properties"]
    assert "attachment" in tools["fetch"].input_schema["properties"]
    assert "operation" in tools["do"].input_schema["properties"]
