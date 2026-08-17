# Deposit-format bench (mise-rolira, workstream C)

The discriminating eval for the deposit-format policy: which format should mise
write tabular content in, measured on real consumers rather than assumed.
Design and rationale: `~/notes/practices/mise/format-bench-design.md` (notes
room). Census weights: `census-findings.md` there. Routing verdict consumed:
`routing-findings.md` (spec 2 — hints work at eye level, not in manifests).

**Why this lives in `tests/bench/`:** the `tests/` tree is outside the plugin
vendor boundary, so bench code commits freely with no suite bump. Files are
deliberately NOT `test_*`-prefixed — pytest must never collect them (runs cost
real money). The `bench-work/` data area (fixtures, transcripts, results) is
generated OUTSIDE the repo at `~/bench-work` — fixtures are synthetic and
regenerable, but transcripts and results are measurements: treat them as data.
Real-document overlays (S3) are local-only and never committed — this repo is
public.

## Pieces

| File | What |
|---|---|
| `render.py` | 7 format arms from one canonical table — format is the only variable |
| `generator.py` | seeded fixtures: S1 long table (200→50k rows), S2 wide, S4 buried, S5 join, sabotage variant; questions + Decimal ground truths to `answers/answers.json` |
| `verify_fixture.py` | independent re-derivation (csv/tsv/json parsers); run `--perturb` FIRST and watch it fail |
| `run_bench.py` | plan → isolated ardoise spawns → stream-json transcripts; idempotent (re-invoke to resume); `--make-smoke-plan` / `--make-pilot-plan` |
| `score_runs.py` | transcripts → results.csv; exact-value scoring; method taxonomy; `--self-test` |
| `ardoise_cwd.sh` | vendored copy of trousse's ardoise.sh + `--cwd` for print mode (upstream: trousse-fawufi) |

## The one-command loop

```bash
uv run --script generator.py                     # fixtures + answers (~6s)
uv run --script verify_fixture.py --perturb      # known-bad control: MUST fail
uv run --script verify_fixture.py                # must pass clean
uv run --script score_runs.py --self-test        # scorer controls
uv run --script run_bench.py --make-pilot-plan
uv run --script run_bench.py --plan ~/bench-work/pilot.json --jobs 4
uv run --script score_runs.py --plan ~/bench-work/pilot.json --results pilot-results.csv
```

## Facts the code embodies (learned, not designed)

- **tools-off means inline delivery.** A subject with `--tools ""` cannot open
  files, so the no-tools arm inlines the deposit into the prompt — which is
  also exactly how the unattended consumer (Garni) receives content.
- **Inline is the expensive arm** (measured 2026-08-17: ~4x an agentic run at
  2k rows on Fable) — agentic turns ride the prompt cache at a discount; an
  inline deposit is a fresh cache write every run. Token accounting must sum
  `tokens_in + cache_read + cache_write`; `usage.input_tokens` alone is a lie.
- **`naive_hit` is a mention flag, not an error flag** — strong subjects
  narrate the bracket judgement and quote both totals. Bracket-mishandling
  = `correct==0 AND naive_hit==1`.
- **`cat manifest.json` is triage, not a paid read** — whole-read patterns
  must target content files or the method labels flatter nobody.
- Models: `fable` = no flag (rides `ANTHROPIC_MODEL` through ardoise's Vertex
  passthrough); `opus` = `--model opus`. Trust the scorer's `model` column
  (init event read-back), never the request.
