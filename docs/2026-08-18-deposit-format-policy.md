# The deposit-format policy — decided by measurement, 2026-08-18

*The numbers behind every verdict: `~/notes/practices/mise/format-league-findings.md` (918 scored runs, three readers, ~$200) plus `census-findings.md` (70 real PDFs) and `routing-findings.md` (spec 2) in the same notes room. Harness: `tests/bench/` in this repo — committed, re-runnable, takes new model strings (the reprofiling battery for the next model generation). Bench data: `~/bench-work/`. Board: mise-rolira.*

## Format verdicts

| Surface | Verdict | The deciding number |
|---|---|---|
| Sheets deposits | **CSV, unchanged** | Accuracy at ceiling on all three readers; +2% tokens vs aligned; and CSV is what `create/overwrite source=` round-trips through — changing it breaks the write-back contract for a rounding-error gain |
| PDF text | **pdftotext -layout aligned** (mise-mitoki implements) | Census: 97.4% verbatim survival vs markitdown 93.0%, docling 75.7%; 55× faster; page-true on the 256pp control |
| Minified JSON | **Never a deposit format** | Dominated on every axis: only format with real accuracy misses in flash run-1, +33–41% tokens (Gemini), +64% inline dollars (Claude), and it recruits the query lane spec 2 priced at +21% for no accuracy |
| Markdown tables | **Not adopted, not feared** | +4% tokens on Gemini (the cl100k +23–27% does NOT travel across tokeniser families — measure per consumer family), ceiling accuracy; buys nothing over aligned |
| Dual (aligned + CSV sidecar + README router) | **Not adopted** | 16 tooled runs: zero used only the sidecar, 6 paid to read both forms, accuracy identical; +1% cost for no measured benefit. Re-entry door if one ever earns it: a **typed** sidecar (raw numerics, clean headers) for write-back or sustained-analysis workflows — the bench's sidecar carried the same display strings and so offered engines nothing |
| Vision-only values (~3% of census) | **Region-grain crops + grep-able anchors** (mise-jopohi, in flight) | agsp-winiri (2026-08-18): Antigravity view_file delivers real pixels, canary-proven on 3.6- and 3.7-flash; consuming half agsp-cigene born blocked on our format |

## The router that isn't

**No routing hints ship in deposits, and no manifest router gets built.** Three measurements close it: hints in manifest.json are read and ignored (spec 2: read 15/20, obeyed 0/10 — the router lives at eye level); forcing the query lane costs +21% with accuracy already at ceiling (spec 2); and the 50k tier shows no cost cliff for tools to rescue anyone from (18/18 at $0.31–0.85/run, both Claudes). The real threshold is **arm, not format**: under the estate's standard 1M windows, a 2,000-row aligned deposit is a measured 218k Claude tokens, so ~9k rows hits the window and 50k (~5.5M) is never inline — past ~10k rows a deposit must be consumed with tools or pre-sliced by the producer. At the window's edge, compact formats (TSV ~880k at 10k rows) buy a few thousand extra inline rows — the only place format changes what is possible.

## Consumer-side riders (cheaper than any format change)

- **"Show any working in text"** for no-tools readers: took Flash from 8 computation misses to 364/364 — the single largest accuracy lever measured anywhere in the league.
- **The disclosure line** ("if the data appears internally inconsistent, mislabelled, or corrupted, state that prominently…"): 0/84 unprompted disclosure → 42/42 with the line, accuracy held; shipped into Garni's system instructions 2026-08-18 (agsp-pemefa) and proved both ways — corrupted fixture drew the notice, clean twin raised no false alarm.

## Engine guidance for consuming Claudes

One-shot questions against deposits up to at least 50k rows: **coreutils + jq + stdlib python** — measured as what tooled readers choose unprompted at every scale (DuckDB/Polars: zero uses despite being installed), 18/18 correct at 50k. **DuckDB earns its import ceremony only with a when-clause**: sustained analysis (many questions, one large deposit), genuine multi-file joins, or beyond-memory data. Polars carries a deployment hazard: the standard wheel illegal-instructions on non-AVX2 CPUs (tube, 2026-08-17) — prefer DuckDB as the kept engine. The unmeasured cell, if the daily-driver question returns: ten sequential questions on one 50k deposit, grep-lane vs DuckDB-lane, ~$15 — where the amortisation lines cross.

## Where each edit lands (vendor boundary)

Free tree, shipped by this doc: the policy itself, `tests/bench/` harness. Riding the next publish (mitoki's, most likely): the mise skill's engine when-clause and the dual-README wording correction in bench docs if retained, CLAUDE.md's pointer to `tests/bench/`, and any `instructions.md` guidance. Garni-side riders live on the mit-garni board (pemefa shipped; show-working filed).
