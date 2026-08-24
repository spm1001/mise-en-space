"""mise-bapije step 4's instrument: parallel multi-fetch wall clock + safety checks.

Run twice against the SAME script: once with server.py's _serialized lock in place
(the BEFORE arm), once with it deleted (AFTER). Real stdio server, real Drive
fetches of the three bench-corpus fixtures on planetmodha (the personal-account
default is deliberate — recorded on mise-zidipo).

    MISE_TOKEN_PATH=~/.claude/plugins/data/mise-home/token.json \
        uv run --extra extraction python docs/research/2026-08-24-bapije-concurrency/measure_parallel_fetch.py <label>

Method: one warm solo fetch (token refresh + pool warm-up, untimed), then REPS
timed rounds, each issuing the three fetches CONCURRENTLY via a task group.
With the serializer, wall ≈ sum of the three; without, wall ≈ the slowest one.
Safety after every round: result not error, deposit dir present, content file
non-empty, manifest.json parses.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[3]

FIXTURES = {
    "1VD3kZbc4C_yWlPQ7PVLB7z0e1fIvo8GbjvRUEZwBMSo": "doc--fixture-01",
    "1cnNI80r6xkuZf7JPDkIhEx_uAOInw5OEWmxT4XMLqUM": "sheet--fixture-02",
    "1ls9Ry93qFOE1hVh7rp_WSBLZIYfPUhuVT--3pwxua9A": "slides--fixture-03",
}
REPS = 3


def check_deposits(base: Path, results: dict[str, dict]) -> list[str]:
    problems = []
    for fid, payload in results.items():
        if payload.get("error"):
            problems.append(f"{FIXTURES[fid]}: tool error {payload}")
            continue
        dep = Path(payload["path"])
        if not dep.is_dir():
            problems.append(f"{FIXTURES[fid]}: deposit dir missing: {dep}")
            continue
        content = dep / Path(payload["content_file"]).name
        if not content.exists() or content.stat().st_size == 0:
            problems.append(f"{FIXTURES[fid]}: content file absent/empty")
        try:
            json.loads((dep / "manifest.json").read_text())
        except Exception as e:
            problems.append(f"{FIXTURES[fid]}: manifest unreadable: {e}")
    return problems


async def main(label: str) -> None:
    base = Path(tempfile.mkdtemp(prefix=f"bapije-{label}-"))
    env = dict(os.environ)

    params = StdioServerParameters(
        command=sys.executable, args=[str(REPO / "server.py")], env=env,
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()

            async def one_fetch(fid: str, out: dict) -> None:
                res = await session.call_tool(
                    "fetch", {"file_id": fid, "base_path": str(base)}
                )
                text = "".join(getattr(b, "text", "") for b in res.content)
                try:
                    out[fid] = json.loads(text)
                except json.JSONDecodeError:
                    out[fid] = {"error": True, "raw": text[:200]}

            # Warm-up (token refresh, http pool) — untimed, solo.
            warm: dict[str, dict] = {}
            await one_fetch(next(iter(FIXTURES)), warm)
            print(f"warm-up ok={not warm[next(iter(FIXTURES))].get('error')}")

            walls = []
            for rep in range(1, REPS + 1):
                results: dict[str, dict] = {}
                t0 = time.perf_counter()
                async with anyio.create_task_group() as tg:
                    for fid in FIXTURES:
                        tg.start_soon(one_fetch, fid, results)
                wall = time.perf_counter() - t0
                problems = check_deposits(base, results)
                walls.append(wall)
                status = "CLEAN" if not problems else "; ".join(problems)
                print(f"[{label}] rep {rep}: wall={wall:.2f}s safety={status}")

            print(f"[{label}] walls: {[f'{x:.2f}' for x in walls]} "
                  f"median={sorted(walls)[len(walls)//2]:.2f}s")


anyio.run(main, sys.argv[1] if len(sys.argv) > 1 else "unlabelled")
