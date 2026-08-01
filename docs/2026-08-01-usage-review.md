# The week's real mise usage, reviewed — 2026-07-26 → 08-01

*bon: mise-pojoro. Every misfire, friction, absence and win from a heavy in-earnest week
(CMA/Sky-transaction document digging, debrief hunting, inbox triage), mapped to the surface
that fixes it. Two instruments, paired: `calls.jsonl` (what reached mise) and session
transcripts (what agents were trying to do). Each corrected the other at least once — see
"Instrument notes" at the end.*

## The week in numbers

- **325 calls** in the window (fetch 161, search 131, do 33); steady ~250–290/week across
  the log's four weeks. Gmail-heavy: 62 of 161 fetches (38%).
- **Against the April baseline** (2026-04-09 stock-take: ~375 calls over 19 days): daily
  volume up 2.3× (~20/day → ~46/day). Fetch share unchanged (~47–50%). Search share up
  (33% → 40%). **do() share halved (20% → 10%)** — mise is increasingly a research
  instrument. `replace_text` is still the top write op (13 of 33); the comment loop
  (comment 4 + comment_reply 4) is live — mise-komibu's triage pattern in production.
  Of April's six never-used ops, `draft` and `rename` have come alive; `share`,
  `prepend`, `star`, `label` remain at zero.
- **4 failures in 325 calls (98.8% ok)** — and that number is exactly as misleading as
  the brief predicted. The log's failures are real but the week's actual cost lived in
  what the log cannot see: an id handed out by mise's own cue that 404s, a tab pointer
  stripped before the call, sixteen hand-rolled scripts doing what do() can't.

## The headline: refusals that teach get obeyed in seconds; errors that don't cost minutes

Every teaching-shaped failure this week was followed, within seconds, by exactly the
recovery it prescribed:

| Failure (all Fri 31 Jul) | Teaching? | Recovery |
|---|---|---|
| 07:36 fetch of pasted `#inbox/Ktbx…` Gmail URL | Yes — "use search(…)" | 6 s: `in:inbox newer_than:2d` → thread found |
| 14:28 fetch of pasted `#search/will+tapp+lbs/FMfcgz…` URL | Yes | 4 s: search `tapp LBS` — **the recovery terms were sitting in the URL mise refused** |
| 16:56 reply_draft onto thread already carrying a draft | Yes — guard names the draft id | 34 s: `draft(file_id=r828…)`, same content — exactly as prescribed |
| 16:23 fetch of `18fe27655760c61b` | **No — raw 404** | **7 minutes**, via an unrelated `subject:Digiday` search |

The raw-404 class is the only failure class that leaves callers blind, and it produced the
week's only real detour. Whole-log it's also the largest untaught class (6 of 23 lifetime
failures, including two Gmail **draft ids** (`r-…`) passed to fetch, each retried once
against a permanent 404, then abandoned).

The 16:23 404 deserves its full story, because the mechanism is mise's own: a search
preview's `email_context` cue said `message_id: 18fe27655760c61b` and invites fetching it —
but a message id only resolves as a thread when that message *heads* the thread, and this
was mid-thread. The Fable session diagnosed this itself and filed **mise-saroca** that
evening. What this review adds: the Gmail adapter holds the true `threadId` at search time,
so the cue can simply hand out the right id — or fetch can fall back via `messages.get` on
404. Zero extra calls in the happy path either way.

## Findings, adjudicated

Taxonomy fixed before reading: MISFIRE (wrong tool/params/URL events), FRICTION
(multi-call fumbles a surface change collapses), ABSENCE (Workspace-shaped work done
without mise), WIN (what must not regress). One surface and one verdict each.

| # | Finding | Class | Surface | Verdict → who holds it |
|---|---|---|---|---|
| 1 | Pasted Gmail URLs refused; recovery manual; in one case the URL itself carried the recovery terms | MISFIRE | code | **act** → mise-jujoti + mise-lerulo (filed; this is their production evidence — a Claude already used `rfc822msgid:` manually at 14:32, proving lerulo's route works) |
| 2 | `email_context.message_id` cue invites a fetch that 404s mid-thread | MISFIRE | code (cue) | **act** → mise-saroca, brief enriched with mechanism + fix shape this session |
| 3 | Raw 404s carry no teaching — the only failure class with no recovery route (6 lifetime, incl. draft-ids-as-fetch retried against permanent 404) | MISFIRE | code | **act** → new bon (404s name the likely id type and the next move) |
| 4 | Sheets URL with `?gid=867636828` pasted by Sameer; Claude stripped it to a bare file id (27 Jul 08:57) | MISFIRE | SKILL.md first, then code | **act** → mise-dogape (filed, fully specified; this is its live instance) |
| 5 | A Claude emitted old tool name `mcp__mise__search` → "No such tool available" (27 Jul 21:37); shipped shards/skill still document the `mcp__mise__*` prefix while the harness exposes `mcp__plugin_mise_mise__*` | MISFIRE | instructions.md / SKILL.md | **act** → new bon (prefix references match what the harness actually exposes) |
| 6 | Compound AND-queries missed known docs; Sameer corrected by hand ("did you try 'questbrand debrief'") | MISFIRE | SKILL.md | **act** → fold into mise-hovugo's guidance side: fewer words, `or`-synonyms via raw_query. The notes-side glossary rule ("entry whenever a search costs more than two attempts") is the user-side half, already operating |
| 7 | 46% of searches (61/132) returned exactly `max_results` — half the week worked from truncated sets. Partly deliberate (Sameer's longlist-and-fillet strategy, stated 31 Jul 15:11); partly unnoticed (27 Jul debrief hunts at caps of 20–25). Gmail's recency-not-relevance ordering bites precisely here | FRICTION | cue (exists) + SKILL.md | **watch** → mise-hovugo carries the act half (filed; platform ceiling already documented). Cue coverage is complete since 1.24.0 |
| 8 | **Sheets range read/write absent: ~16 hand-rolled Google-API scripts in one morning** (27 Jul: sheet-read, sheet-write, index-update, cellcheck, idxfix, idx2, idx3…), borrowing mise's own token; same pattern again 30 Jul (wc-row-read/write/verify) | ABSENCE | code | **act** → mise-vadoko + mise-bazuvo (filed; this quantifies them as the board's top capability gap) |
| 9 | poppler-utils undeclared → PDF/pptx thumbnails silently absent during the consultancy-deck extraction (31 Jul 15:55) | FRICTION | packaging | **act** → mise-releko (filed; live hit recorded) |
| 10 | Hand-rolled copy + Gmail-attachment→Drive scripts (27 Jul) | ABSENCE | code | **benign now** — do(copy) shipped within the week (hezuke); raw= + file_path= pairing shipped (buzafo). Note: that session ran plugin 1.23.0 against a 1.26.x repo — some absence was version lag, not capability gap. `/batterie:update` cadence is the existing control |
| 11 | No WebFetch/curl bypasses of Workspace found anywhere in the window — every Workspace-shaped retrieval reached for mise | WIN | — | routing guidance works; guidance aimed at "wrong tool reach" is ballast for gen-5 models |
| 12 | Zero zero-result searches (0/132); raw_query adopted 25×; operator-refusal never needed to fire | WIN | — | query construction is good; don't add guidance weight here |
| 13 | Draft loop end-to-end: superseded-draft guard fired once, was obeyed in 34 s; `is:draft` checks appear unprompted in morning triage | WIN | — | mise-sasivo shipping validated in production |
| 14 | Klartext/parser bakeoff (31 Jul, separate evaluation): markitdown stays — its PDF path already runs pdfplumber; swap question closed | WIN | docs | record as "Considered and not pursued" per that chat's process note |
| 15 | Same evaluation: docling ships prompt-injection scrubbing (hidden text, zero-size fonts, off-page content); mise pipes stranger-authored Gmail attachments into agent context with no equivalent control | ABSENCE | code | **act** → new bon (injection-surface scrub for fetched content), security-shaped |
| 16 | "The harness seems to be stopping you" (31 Jul 15:51) — Opus self-diagnosed: "it's not the harness, it's me — I've been ending turns to report each find" | model-guidance | rasa/carte | **watch** → turn-pacing on long grinds persists in gen-5; feed to guidance-streamlining work |

## Answers for the three consumers

**Surface allocation.** The week's evidence puts the weight where the brief suspected:
SKILL.md is the under-used surface (URL-whole-passing, query construction, cap awareness in
triage loops — items 4, 6, 7); cues are nearly right but must never hand out an id their own
fetch can't take (item 2); the tool-description budget needs nothing new; code owns the id
classes end-to-end (items 1–3) and the one big missing verb family, Sheets ranges (item 8).

**Google-MCP route-vs-build** (for docs/2026-08-01-google-mcp-spike.md): usage frequency now
quantified — search 40%, fetch 50%, gmail 38% of fetches, and the single verb family whose
absence drives users to raw APIs is Sheets range read/write. The parser question is closed
independently: markitdown stays (item 14).

**Gen-5 guidance streamlining** (rasa/carte): the failure modes that PERSIST are turn-pacing
on long grinds (item 16) and URL-stripping before tool calls (item 4). The ones that have
NOT persisted — and whose guidance is now ballast: wrong-tool reach (item 11), query
construction (item 12), zero-result flailing (item 12). Teaching errors are followed
precisely, which means investment in error *text* beats investment in pre-emptive guidance.

## Instrument notes (for the next reviewer)

- `calls.jsonl` never records `base_path` — an all-None distribution is the logger, not the
  callers (they pass it faithfully; transcripts show it).
- The log is shared, live state: a parallel bench session appended probes *inside this
  review's window* during the review. Analysis ran on a frozen snapshot; probe-marked
  traffic ('probe', 'throwaway', 'smoke test') was segmented out.
- The log-only view called the 16:23 404 "no recovery, moved on"; transcripts showed
  recovery 7 minutes later by another route. Neither instrument suffices alone — the
  pairing is the method.
- Rejection counting from transcripts must match the current refusal string ("The user
  doesn't want to proceed…"), not the documented older one ("User rejected tool use").
  True rejections this week: 2, both one benign mid-fan-out redirect.
- No mise-home calls all week — work-flavour numbers are the whole population.

## Sources

- calls.jsonl snapshot: `/tmp/pojoro/calls-snapshot-2026-08-01T1050.jsonl` (frozen 10:50);
  analysis scripts `/tmp/pojoro/*.py`
- Seed sessions (Sameer-remembered, confirmed): Opus 31 Jul `~/.claude/projects/-home-modha-notes/ef3a3e17-…jsonl`
  (parsed: `~/notes/raw/claude/code/`), Fable 31 Jul `…-sky-transaction/cbdfa6e9-…jsonl`
- 27 Jul CMA-RFI session (absence evidence): `~/.claude/projects/-home-modha-notes/75692a22-…jsonl`;
  hand-rolled scripts `~/scratch/{sheet-read,sheet-write,index-update,…}.py`
- April baseline: `.bon/handoffs/2026-04-09-82226c2a.md`
- Klartext evaluation: `~/notes/raw/claude/chats/2026-07-31 1639 — Evaluating Klartext against Mise/`
- Field reports absorbed: mise-saroca, mise-gemowa; handoff `.bon/handoffs/2026-07-28-c16cdcfc.md`
