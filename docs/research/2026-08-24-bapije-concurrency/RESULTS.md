# mise-bapije measurement record — 2026-08-24

Instrument: [`measure_parallel_fetch.py`](./measure_parallel_fetch.py) — real stdio server
(`server.py` from the working tree), real Drive fetches of the three bench-corpus fixtures
(doc FIXTURE-01, sheet FIXTURE-02, slides FIXTURE-03) on planetmodha, one untimed warm-up
(token refresh + pool), then 3 timed rounds of the three fetches issued concurrently.
Safety per round: tool result not error, deposit dir present, content file non-empty,
manifest.json parses.

| Arm | Server state | Walls (s) | Median | Safety |
|---|---|---|---|---|
| before | `_serialized` lock in place (as shipped in 1.71.0–1.72.0) | 7.02, 5.14, 5.00 | **5.14s** | CLEAN ×3 |
| after | lock deleted + three guards in (this change) | 2.89, 2.32, 2.36 | **2.36s** | CLEAN ×3 |

**Release-note number: parallel 3-fetch 5.14s → 2.36s median (2.2×); wall tracks the
slowest fetch, not the sum.** The first rep in each arm is the warmer-adjacent outlier;
medians are the honest figure.

Guard sensitivity controls (run the same day, inline — the guards' unit tests in
`tests/unit/test_concurrency_guards.py` were shown able to fail):

- old `exists()`-loop deposit naming under the 8-thread hammer: collided **20/20 trials**;
- unlocked refresh stampede: **8 refreshes** where the lock allows 1;
- the router-lock test carries its own positive control in-file (arm B asserts overlap).
