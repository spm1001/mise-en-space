# Research brief: what should "best for agents" actually mean?

> **SUPERSEDED — this is v1, kept as a dated capture.** The live brief is **v2.1** at
> `~/notes/practices/mise/prepped-for-agents-brief.md` (same-day Chat review → v2, Deep
> Research sweep folded → v2.1; TeleTables verified against the primary source 2026-08-16).
> What changed: the +23–27% headline was a pipeline/format conflation (minimal pipes cost
> only +6%; padding is the tax), the 8/8 comprehension tie was a ceiling effect, the token
> league table has now RUN across cl100k/o200k/Gemma-SP (ranking transfers; padded pipes are
> *worse* on Gemma), and the familiarity principle rules out exotic formats. Workstream A is
> done (report archived beside the v2.1 brief in `sources/`); next are B (estate census) and
> C (the bench) — ready-to-run specs sit in the same notes room. *(Banner added 2026-08-17,
> jaceja reconciliation.)*

*Drafted 2026-08-14, from the giwawa/wujoga/mitoki measurement night. Status: sketch for Sameer to react to. Seed evidence at the bottom.*

## The provocation

We caught human legibility being a bad proxy for agent legibility, in both directions in one evening. Markdown pipe tables — the format we'd have sworn was "highest quality for the agent" — cost 23–27% more tokens than whitespace-padded columns and bought zero comprehension gain on a hard table-reading eval. The padding we'd have called janky compresses beautifully, because BPE tokenisers eat runs of spaces as single tokens while `| --- |` furniture fragments expensively.

The recalibration this forces: a document going to an agent exists in three forms — the rendered page a human sees, the plaintext bytes we inspect, and the token sequence the model actually consumes. Mise has been optimising form two and judging by form one. **The interface is form three, and we have almost no intuitions about it.**

## The premise at stake

Mise's promise is "everything prepped, in its place, ready for Claude to cook with." If we don't know what token-optimal prep looks like, that promise is aspiration, not engineering. And "best for agents" is not one number — a deposit is consumed at several lifecycle stages, and formats plausibly rank differently at each:

| Stage | What matters | Plausible winner |
|---|---|---|
| **Grep** (find the region without reading) | line-oriented regularity, one record per line | aligned columns / TSV? |
| **Read into context** (token cost) | tokeniser compression | space-padded / TSV? |
| **Reason over** (comprehension, cross-referencing) | unambiguous label↔value binding | unknown — tonight measured a tie |
| **Quote out** (citations a human can ⌘F in the source) | snippet stability between deposit and source | whatever preserves source strings verbatim |
| **Write back / round-trip** (edit, diff) | canonical parse | CSV / structured |
| **Tool-call economics** (turns, not tokens) | fewer Read/grep dances per question answered | file-per-tab? offsets in manifest? |

A format that wins tokens but loses grep — or needs three tool calls where one did — is a net loss. The bench must score the whole row, not one cell.

## Research questions

1. **The league table.** For the same real datasets (a financial table, a wide sheet, a log, a schedule), what is the token cost of: markdown pipe table, aligned whitespace, CSV, TSV, JSON pretty, JSON minified, YAML, XML/HTML table, key-value lines? Measured across tokeniser families — OpenAI BPE (cl100k/o200k), Anthropic, and **Gemini's SentencePiece** — because our consumers span all three, and whitespace compression is exactly where tokenisers differ most. *Tonight's numbers are cl100k only and must not be trusted across families.*
2. **Does structure help the model at all, and when?** Tonight: no, on one page at Haiku tier. Where does that break — wrapped multi-line cells, wide tables (20+ columns), long-range column tracking deep in context, weaker/stronger models? There is published table-QA literature claiming format effects in both directions; a sweep should collect and weigh it against our own bench rather than trusting either.
3. **What else looks janky but tokenises well?** Candidate sleepers to test: TSV (a tab is one token, no alignment padding at all — and human readability, while second-order, is real: tabs degrade gracefully in a proportional font, where space-padding only ever aligns in fixed-width), fixed-width without markdown ceremony, unix `column -t` output, minified JSON vs pretty (indentation is pure token tax — but does the model comprehend minified as well?), digit-grouping effects (does `11,651.3` tokenise worse than `11651.3`, and does normalising numbers corrupt quote-citation?).
4. **The grep axis, measured not vibed.** Same questions answered via search-then-read instead of read-everything: which formats let a regex land on the right row first try? Count tool calls and total tokens per answered question, not tokens per file.
5. **Lifecycle Pareto set.** Is there one format that's good enough everywhere, or should mise deposit *two* forms (e.g. aligned-text for reading + CSV for round-trip — it already does per-tab CSVs for sheets), and does the manifest need to say which form to use for what?
6. **Stability.** Tokenisers and models churn. What's the smallest standing harness (tonight's pattern: matched slices, exact-value QA, cheap readers, token counts) that keeps the answer current instead of banked-and-rotting?

## Method sketch

Literature sweep first (table-representation-for-LLM papers, tokeniser docs, practitioner benchmarks), then a reproducible micro-bench over **our own estate's documents** — the corpus that matters is IR PDFs, board packs, budget sheets, not academic datasets. Reader models: Haiku (cheap, discriminating), Sonnet, and Gemini Flash (Garni's family). Score per lifecycle stage per the matrix above. Deliverable: a short findings doc plus a deposit-format policy for mise with numbers attached — and the harness checked in so the next tokeniser generation can re-run it.

## Seed observations (2026-08-14, cl100k, Banijay interim annexes)

- Full document: markitdown pipe-table output 55,882 tokens vs pdftotext -layout 45,573 (**+23% for pipes**), despite markitdown having *fewer characters* (217,592 vs 245,886).
- Matched page slice: 1,158 vs 910 tokens (**+27%**).
- Comprehension eval, 8 exact-value questions with column traps, 2×Haiku per format: **8/8 all four runs** — format made no difference, including where markitdown had scrambled the header rows.
- Wall-clock context: pdftotext 1.0s vs markitdown 55.1s on a 256-page report.
- Known limits: one page, one document, one tokeniser, one reader model, 8 questions.
