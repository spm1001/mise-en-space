# 2026-09-01 — Google Workspace MCP (Developer Preview) measured against mise (mise-natila)

**What this is.** The mise-natila card asked for Google's official Workspace MCP servers (Developer Preview Program) to be measured against mise on: tool-surface context cost, content delivery, search truncation honesty, and Shared Drive coverage. The run happened on 2026-09-01: all eight `*mcp.googleapis.com` servers re-harvested live, six exercised with real calls, and the synthetic bench corpus (4 fixtures, 14 text probes + 1 disclosure probe — `~/notes/practices/mise/bench-corpus/`) run through both sides and scored by `fact_recall.py`. The write-up then went through an adversarial cold review playing the rival maintainer the card's falsifier names; one headline delivery claim was refuted and replaced with a measured three-point configuration curve, and every corrected figure recomputes from an archived run.

**Where the results are.** Google's servers are pre-GA, and the Developer Preview Program's terms scope findings to participants' own decisions. This repo is public, so the comparison note and all raw runs live in the private estate: `~/notes/practices/mise/google-dpp-recheck-2026-09-01.md` with raw runs in `~/notes/practices/mise/natila-2026-09-01/` (harvests, per-call payloads, driver scripts, deposit trees). The earlier landscape spike is `docs/2026-08-01-google-mcp-spike.md`.

## Mise-side facts from the run (ours to publish)

- **Tool surface, 2026-09-01 working tree (`eb68863`):** 3 tools, **2,802 tokens** (o200k_base, compact JSON) — up from 2,334 on 2026-08-17. About two-thirds of the growth is feature (the comment-anchor and suggest lanes grew `do` to 42 params), the rest SDK vintage (mcp 2.x auto-mints a small outputSchema per tool). Tiering-by-aggregation means every new `do` operation lands on the shared surface; the cost is visible and worth watching.
- **Fact recall:** 14/14 text probes across all four fixtures, vision-only disclosed via the selective thumbnail — same result as the 2026-08-24 run, now reconfirmed on the current tree in guest mode (`MISE_TOKEN_PATH`).
- **In-context cost per fetch (cues response):** 735–1,513 chars per fixture; deposit text artefacts 411–1,006 tokens per fixture. Search fan-out over three sources: 4,345 chars in context for 40 deposited results, truncation stated in words (`drive_truncated`, `preview_partial`).

## Method notes a future comparison builder should read first

Generic — none of these depends on any vendor's pre-GA specifics:

1. **Preview surfaces drift in weeks.** Tool counts, enum values and query grammars all moved between our 1 Aug and 1 Sep measurements — two of our own August invocations now error. Re-harvest and re-probe on the day you publish; date every number; treat any comparison against a preview surface as having a shelf life of days.
2. **Control the permission plane before scoring a content layer.** Some layers (comments especially) are invisible to lower share roles *without an error* — a miss scored under the wrong grant is a grant artefact. Prove the caller can reach the layer with the raw platform API before scoring any MCP surface on it.
3. **Count the text that enters context, not the wire bytes.** Nested serialisation (JSON-RPC envelope → JSON wrapper → stringified resource) can double or triple apparent size; only the text the client injects is the cost the model pays. Report both if in doubt, and say which is which.
4. **Don't trust HTTP status codes alone.** We observed complete, valid JSON-RPC results riding inside HTTP error bodies. A harness that drops error-status responses unread will measure outages that are not there.
5. **Expect error payloads you would not want quoted.** Malformed inputs can return internal traces as the tool result. Budget for redaction before archiving raw runs anywhere public.
6. **Cost is a curve, not a number** (design note §7.1, reaffirmed): definition cost, per-call payload, and total context to task completion each flatter a different architecture. This run accounts deposit reads *into* mise's totals for exactly that reason.

Scored deposits, drivers and the full comparison table: private paths above. Board: verdict recorded on mise-zidipo; this card mise-natila.
