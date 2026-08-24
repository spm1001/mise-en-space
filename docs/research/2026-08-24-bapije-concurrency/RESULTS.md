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
| after | lock deleted + three guards in (first cut) | 2.89, 2.32, 2.36 | **2.36s** | CLEAN ×3 |
| after-final | + the cold-review guard extensions (do(source=) locks, gmail resolved-id inner take, single-flight mint, identity-cache reorder) | 3.50, 2.37, 1.85 | **2.37s** | CLEAN ×3 |

**Release-note number: parallel 3-fetch 5.14s → 2.37s median (2.2×); wall tracks the
slowest fetch, not the sum.** The first rep in each arm is the warmer-adjacent outlier;
medians are the honest figure. The after-final arm shows the cold-review guard
extensions cost nothing on the different-id path (2.36 → 2.37s, noise).

**Cold-review round (same day).** A fresh-context refuter attacked the thread-safety
negative with the audit evidence and REFUTED it three ways, all fixed the same hour:
do(create/overwrite, `source=`) read/rewrote deposit folders unguarded (torn read into
a live Doc; stale-manifest clobber) — every source reader now takes
`deposit_lock_for_source()`, keyed on the manifest's resource id so it converges with
the fetch path's lock (convergence pinned by test); a bare Gmail MESSAGE-id spelling
held a different lock than the THREAD-id spelling over one folder — `fetch_gmail` now
takes a second, inner take on the resolved thread id (RLock: same-spelling free); and
`get_sync_client()`'s check-then-act mint could construct two clients — now
double-checked under a lock (test: 8-thread burst, one mint). Two smaller windows
also closed: identity caches now clear BEFORE the dead-grant credential swap (a
sibling's window reads identity-unknown, never the old account), and the profile
cache clears on account swap too. Confirmed non-findings from the same review:
recursive folder fetch writes ONE deposit (listing-only traversal — no per-child
chimera window), calls.jsonl and the benign caches held, and cross-process races
(two servers, one cwd) remain out of scope exactly as the old serializer left them.

Guard sensitivity controls (run the same day, inline — the guards' unit tests in
`tests/unit/test_concurrency_guards.py` were shown able to fail):

- old `exists()`-loop deposit naming under the 8-thread hammer: collided **20/20 trials**;
- unlocked refresh stampede: **8 refreshes** where the lock allows 1;
- the router-lock test carries its own positive control in-file (arm B asserts overlap).
