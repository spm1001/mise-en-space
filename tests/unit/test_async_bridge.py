"""async_bridge is the only door for running async from sync (mise-pagigo).

Bare asyncio.run() raises RuntimeError on a thread that already runs an event
loop. On mcp 1.x every sync tool body was such a thread, and the two fail-open
adapters that called it swallowed the error — thread-a browser resolution was
dead through the live envelope from birth (mise-wamoco). mcp 2.x runs tool
bodies on worker threads, which HIDES the hazard rather than removing it: any
future path on a loop thread (an async tool, a resource handler, a lifespan
task) re-arms it silently. So shipped code routes through
async_bridge.run_async_blocking, and this file enforces that with the same
mechanical-grep idiom test_architecture.py uses for layer imports.
"""

import asyncio
import pathlib

import pytest

from async_bridge import run_async_blocking

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
POLICED_DIRS = ["extractors", "adapters", "tools", "workspace", "resources"]
THE_ONE_DOOR = "async_bridge.py"


def _shipped_python_files() -> list[pathlib.Path]:
    files = [p for d in POLICED_DIRS for p in (REPO_ROOT / d).rglob("*.py")]
    files += [p for p in REPO_ROOT.glob("*.py")]  # root shared utilities + server.py
    return files


def test_asyncio_run_appears_only_in_the_bridge() -> None:
    offenders = []
    for path in _shipped_python_files():
        if path.name == THE_ONE_DOOR:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if "asyncio.run(" in line and not line.strip().startswith("#"):
                offenders.append(str(path.relative_to(REPO_ROOT)))
                break
    assert not offenders, (
        f"bare asyncio.run() outside {THE_ONE_DOOR}: {offenders} — route it "
        "through async_bridge.run_async_blocking (mise-pagigo; the hazard it "
        "guards is recorded in mise-wamoco)"
    )


async def _answer() -> int:
    return 42


async def test_bridge_survives_a_running_loop() -> None:
    """The hostile environment itself — this test runs INSIDE an event loop.

    The control comes first: prove the bare call really does die here, so the
    bridge's success below is evidence rather than a vacuous green.
    """
    doomed = _answer()
    with pytest.raises(RuntimeError):
        asyncio.run(doomed)
    doomed.close()  # silence the never-awaited warning

    assert run_async_blocking(_answer()) == 42


def test_bridge_works_with_no_loop() -> None:
    """The ordinary case: no loop on this thread, plain asyncio.run inside."""
    assert run_async_blocking(_answer()) == 42
