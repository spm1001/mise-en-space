# MCP 2.x probes — 2026-08-23 session artefacts

Referenced by bon closing notes (mise-vucufu, mise-wamoco, mise-huwubi). Copied
from ~/scratch/elicit-probe (the ephemeral zone) so the references survive.

| File | What it proved |
|---|---|
| `loopthread_v1.py` / `loopthread_v2.py` + `drive_loopprobe.py` | Over real stdio: a sync tool body sees a running event loop on mcp 1.x (asyncio.run → RuntimeError) and a clean worker thread on 2.x — the mise-wamoco mechanism, both directions |
| `drive_wamoco.py` | The three-arm experiment driver: fetch a thread-a URL through any cache version's envelope; v1 refused (silent fallback), v2 resolved |
| `probe_server.py` + `selftest.py` + `mcp-config.json` | Elicitation capability probe: CC declares the capability, renders a real form dialog, and the model provably cannot see or supply the answer (mise-huwubi candidate 1, CONFIRMED). selftest is the in-memory known-positive control |
