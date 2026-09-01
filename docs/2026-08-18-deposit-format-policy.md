# The deposit-format policy — decided by measurement, 2026-08-18

*Depends on: `TOK-JSON-TAX`, `TOK-MD`, `COMP-RIDER`, `DISC-RIDER`, `REACH-ENGINES`, `REACH-FORMAT`, `HINT-PLACEMENT`, `CAP-INLINE`, `COST-ARM` — per-rig values in `~/notes/practices/model-profiles/`. When a rig requalifies and any of these move, this policy is stale by exactly that list (the runbook's diff step).*

*Requalification log — 2026-09-01, rig `fable-5-1--claude-code`, T1 only (22 inline cells, same fixtures): none of the cited facts moved — TOK-JSON-TAX +63% (was +64%, same deposits), CAP-INLINE unchanged by construction (deposit tokens identical; the 5.1 briefing is 3,904 tokens longer). DISC-INLINE rose to 4/4 but this policy cites DISC-RIDER. COST-ARM, REACH-*, HINT-PLACEMENT await T2 on that rig. Policy not stale. Datasheet: `rig--fable-5-1--claude-code.md`; evidence `~/notes/practices/mise/format-bench-fable-5-1-2026-09-01/`.*

*Requalification log — 2026-09-01 late evening, rig `fable-5-1--claude-code`, T2 (the league's 73 tools cells, same fixtures, cell-for-cell against Fable 5 and Opus 5): two cited facts moved and the policy holds. REACH-ENGINES moved in composition — 5.1 writes a script (scorer: programmatic 42/73, Fable 5 9/73) where Fable 5 grepped or read the whole file (read-whole 1 vs 18); engines by transcript grep/rg 62, python 31, awk 27, jq 6, Polars 1 (`uv run --with polars`, correct), DuckDB 0 — so the engine guidance below ("coreutils + jq + stdlib python"; "DuckDB/Polars: zero uses") describes 72/73 and its stdlib-python leg is what the shift reaches for; the "zero uses" parenthetical is a Fable 5 fact, now dated. REACH-FORMAT weakened, same direction — json-min recruits querying 6/19 (was 11/19), and 6 of 5.1's 7 queried cells are json-min; the json-min verdict rests on tokens and dollars first, so it stands. COST-ARM unmoved in substance (tools cheaper on every shared cell; 1.7× on the 18 shared hard-slice cells vs Fable 5's 1.5× there; the 4–6× figure is S1-scale and smoke-checked only at 4–5×). Watch, not a change: one 5.1 dual cell consumed the CSV sidecar directly (Polars over `content_campaigns.csv`), which the dual row's "zero used only the sidecar" (16 Fable 5 runs) did not see — one cell, accuracy identical, cost $0.71; re-read the dual verdict if a second appears. COMP-TOOLED 73/73 (Fable 5 73/73, Opus 5 72/73). Policy not stale. Evidence: `compare-t2.txt`, `adjudications-t2.json` in the same archive dir.*

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
