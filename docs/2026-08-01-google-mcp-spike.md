# Google Workspace MCP servers — landscape + head-to-head spike (2026-08-01)

State: **paused at an external gate** — Workspace Developer Preview Program acceptance for
`sameer.modha@itv.com` (applied 2026-08-01 morning). Everything else is staged; resume takes
one command (below). Tracked as a bon on this repo's board (search `bon list` for the
"head-to-head" item).

## The landscape finding (what changed this summer)

- **Google shipped managed Workspace MCP servers** (Cloud Next '26, Developer Preview):
  per-product endpoints `{gmail,drive,docs,sheets,slides,calendar,chat}mcp.googleapis.com/mcp/v1`
  plus `people.googleapis.com/mcp/v1`, and a **Universal Search** server
  `workspacemcp.googleapis.com/mcp/v1` (ONE tool, `search_corpus`, fanning out server-side
  across Gmail/Drive/Calendar/Chat, degrading to whatever scopes were granted).
  Auth terminates at `accounts.google.com` (RFC 9728 metadata live on every endpoint);
  **any OAuth client works** — you enable the `*mcp` services in your own GCP project.
- **Anthropic re-platformed its built-in Gmail/Calendar connectors onto Google's servers**
  between 2026-05-04 and 2026-07-07. Evidence: a Cowork session file
  (`~/.config/Claude/local-agent-mode-sessions/.../local_860a9c47*.json`,
  `remoteMcpServersConfig`) carries the connector named "Gmail" with
  `url: https://gmailmcp.googleapis.com/mcp/v1`; the old proxy hostnames
  `gmail.mcp.claude.com` / `gcal.mcp.claude.com` are dead (CNAME residue to
  ghs.googlehosted.com, no TLS mapping — verified from kube with a live control), while
  `microsoft365.mcp.claude.com` still serves (the Anthropic-proxy-on-GCP fleet persists where
  the provider has no managed MCP). The ITV gate is unchanged: the built-in connector still
  authenticates with Anthropic's OAuth client, which ITV's app access control blocks.
- **The new possibility**: an ITV-Internal OAuth client (mise's exact trick) pointed at
  Google's hosted servers = sanctioned, zero-infrastructure remote Workspace MCP —
  claude.ai custom connectors (redirect `https://claude.ai/api/mcp/auth_callback`),
  Antigravity, Claude Code, anything MCP. This supersedes the parked "Lite Mise" remote
  deployment path (tokiju/winala/sefepo): every objection that parked it was about custody,
  and Google's design leaves us custody of nothing.

## Gates hit (in order), for whoever re-runs this

1. `Workspace MCP API has not been used in project 413373784317` → fixed:
   `gcloud services enable workspacemcp.googleapis.com gmailmcp.googleapis.com drivemcp.googleapis.com --project=413373784317`
   (all three now enabled on `mit-workspace-mcp-server`).
2. Bare `"The caller does not have permission"` on every `tools/call` → **Developer Preview
   Program enrolment** (application at developers.google.com/workspace/preview; accepted
   same day — four ITV projects blessed, see Bench state). `tools/list` gating is
   inconsistent: free on workspacemcp pre-enablement, 403 on calendarmcp/docsmcp without
   their services enabled.
3. **Project footprint change, deliberate (2026-08-01):** all eight MCP services
   (`workspacemcp`, `gmailmcp`, `drivemcp`, `docsmcp`, `sheetsmcp`, `slidesmcp`,
   `calendarmcp`, `chatmcp`) are now **enabled on `mit-workspace-mcp-server`**
   (413373784317), by Sameer's authorisation, for this spike and the mise-gofuvu work.
   An auditor finding them unexplained should read this doc.
3. Note: preview Program Terms forbid shipping Pre-GA features in public applications.

## Bench state

**Mise side (measured, this box, `mise-bench-summary.json`):** composed drive+gmail+calendar
search via `do_search`, 4 estate queries × 2 runs: cold 1.8–3.4s, warm 1.3–1.7s, 20+20
results with truncation cues. Quality note against ourselves: **Gmail results are
recency-ordered, not relevance-ordered** — one fresh broad thread topped 3 of 4 queries.
Attribution (verified 2026-08-01): this is a **platform ceiling, not a mise choice** — the
Gmail API has no ordering parameter (newest-first is all it sells), and Google's own gmailmcp
`search_threads` carries the identical constraint. Drive is unaffected: mise passes no
`orderBy` on fullText queries, so Drive results already carry Google's relevance ranking.
The one place relevance-ranked Gmail could exist is `search_corpus` re-ranking server-side —
which this bench now decides; disclosing the ordering semantics on mise's own surface is
tracked as bon mise-hovugo.

**Google side — MEASURED 2026-08-01 (preview acceptance landed same day; four ITV projects
blessed: mit-dev 1018230309720, mit-workspace-mcp-server 413373784317, itv-mit-slides-formatter
780647236756, itv-mit-shared 959051780527).** Driver: `2026-08-01-google-mcp-spike/gmcp.py`
(`GMCP_BASE` env selects the server; refreshes mise's token in-memory, never prints it).

### Head-to-head results

**Latency: a wash.** `search_corpus` 1.15–3.33s across 4 queries × 2 runs vs mise composed
1.32–3.40s. Google's in-network fan-out buys no wall-clock advantage.

**Gmail ranking: Google wins decisively — `search_corpus` relevance-ranks Gmail, measured.**
Mise's recency artefact (one fresh broad thread topping 3 of 4 queries) vs Google's
per-query on-topic tops ("Hillary's x WPP - Region Lift", "Fwd: Clean Room Agreement - ITV",
"Document shared: ViewersLogic × Outcome Planner" promoted from mise's 3rd/recency).
Non-monotonic in time → ranker, not cursor. **This is the capability mise structurally
cannot build client-side** (whole-corpus candidate recall needs the index; the API exports
a filter). Second clear win: **calendar recall 4/4 queries vs mise 0/4** — mise's ±7-day
calendar window is useless for content queries.

**Payload economics: mise wins massively.** 205–264KB inline JSON per search (30 items ×
~2–27KB each, full message snippets) vs mise's ~2KB cued summary + deposit.

**Metadata: mise wins.** Their gmail message objects carry `subject, sender, snippet,
recipients, viewUrl` — **no timestamp, no labels, no unread, no has_invite**. An agent
cannot tell fresh from stale without another call.

**Honesty: mise wins, and their gaps are the accept-and-drop pattern measured externally:**
`pageSize: 20` silently ignored (fixed ~30 back, ~10/corpus); no truncation signal, no
totals, no pagination (declared unsupported while `pageToken` sits in the schema); and
`read_file_content(includeComments=true)` **returned zero comment content on a doc with 10
open comments** — a silent no-op on the exact surface mise treats as first-class.

### Fetch fidelity (same docs, both fetchers)

Text extraction is **near-identical** — Google's `fileContent` (8,324ch) vs mise content.md
(8,388ch) on the bon-estate doc; both almost certainly ride the same `text/markdown` export
engine. Differences: mise adds tab headings, blockquote recovery, deposits + manifest +
metadata; JSON escaping ~2×'s their payload. **Smart chips fumbled by both, differently**:
mise drops people-chips silently (empty cell), Google renders each as the literal word
"Person" (presence without identity). **Checkbox loss in table cells: both lose them; only
mise warned** (count-mismatch guard). Tick-state face-off inconclusive (doc had 0 ticked
boxes; both rendered 67 `[ ]` consistently). read_file_content is fast: 0.21–0.29s.

### Sheets write probe (vadoko/bazuvo evidence)

`update_values` runs **USER_ENTERED semantics** (undocumented): `=HYPERLINK(...)` strings
become real formulas (live link), bare URLs auto-link. Markdown links stay literal; embedded
URLs stay dead; **no chipRuns, no textFormatRuns — rich-text/smart-chip writing does not
exist on their surface**. So bazuvo mechanisms 3/4 need REST `batchUpdate` wherever they're
built; their MCP only proves the vadoko range-write semantics. `suggest_time` works (0.22s)
but returns raw freebusy gaps incl. a 900-minute overnight "slot" — not meeting-shaped
without the unexplored `preferences` param.

### Architecture conclusion the data wrote

**Their ranker inside our envelope.** Mise `search` gains an optional `search_corpus`
backend for candidate discovery: take their ranked IDs, hydrate top-K with mise's own
metadata (dates/labels/unread — one cheap batch), deposit, return the 2KB cued summary.
Google supplies what only Google can (whole-corpus ranking, all-time calendar recall);
mise supplies everything they left out (economics, metadata, honesty, comments). Calendar
*content search* should also ride this backend — it beats extending mise's ±7d window.

## Complete tool map (all eight servers, harvested live 2026-08-01)

All `*mcp` services are now enabled on `mit-workspace-mcp-server` (413373784317), so
`tools/list` works everywhere; only `tools/call` awaits preview acceptance.

| Server | Tools |
|---|---|
| workspacemcp (1) | search_corpus |
| gmailmcp (13) | create_draft, list_drafts, get_thread, get_message, search_threads, label/unlabel_thread, label/unlabel_message, apply_sensitive_thread/message_label, list_labels, create_label |
| drivemcp (8) | search_files, read_file_content, download_file_content, get_file_metadata, get_file_permissions, list_recent_files, copy_file, create_file |
| calendarmcp (9) | search_events, list_events, get_event, list_calendars, **suggest_time**, create_event, update_event, delete_event, **respond_to_event** |
| sheetsmcp (9) | get_values, get_spreadsheet, update_values, **update_formulas**, append_values, insert_dimension, batch_clear_values, copy_sheet_to_another_spreadsheet, update_spreadsheet |
| chatmcp (4) | search_conversations, search_messages, list_messages, **send_message** |
| docsmcp (2) | read_doc, update_doc |
| slidesmcp (2) | read_presentation, update_presentation |

Revisions this forces to the "thin views" verdict:

- **Calendar is where Google's MCP beats mise outright**: full event CRUD, RSVP
  (`respond_to_event` — the write mise deliberately declined in pinodi), and
  `suggest_time` (find-mutual-availability — with universal search, one of only two
  genuinely *composed* tools in the estate). Mise's calendar is search-only, ±7 days.
  Under the two-tier strategy, calendar-write is Google's slot; mise should not grow it.
- **Sheets MCP lands on two open board items**: range-addressed, formula-aware editing
  (update_values/update_formulas/append_values/insert_dimension) is `mise-vadoko`'s
  entire ask and half of `mise-bazuvo`'s. Both briefs now carry the cross-reference —
  decide route-vs-build when the preview clears (and probe whether their write path
  produces rich-text links/smart chips, bazuvo's actual want).
- **Chat's send_message is the only direct send anywhere in the estate** — Gmail
  stays drafts-only while Chat sends. A deliberate asymmetry worth remembering
  when reasoning about their safety posture.
- Docs and Slides stay thin (read + raw batchUpdate).

## Ergonomics findings from the live schemas (`gmcp-tools-*.json`)

Worth stealing:
- **Context-cost dials per read**: `messageFormat FULL/MINIMAL/METADATA_ONLY`,
  `DRAFT_VIEW_METADATA_ONLY` (framed as excluding *sensitive* content — triage without
  reading bodies), `excludeContentSnippets`.
- **Risk taxonomy at the tool boundary**: trash/spam quarantined into `apply_sensitive_*`
  tools with `destructiveHint: true`; ordinary tools' descriptions route the model there.
  Machine-readable safety a harness can hang policy on.
- **Model-directed descriptions**: anti-hallucination ("NEVER guess... a fileId"),
  search-first workflow steering, universal→per-product fallback routing.

Their gaps (≈ mise's moat, item for item):
- `read_file_content` output format **explicitly unstable** ("will change over time");
  content "may be incomplete for very large files" with no visible truncation cue.
- **No attachment content path at all** (metadata only); drafts can't carry attachments;
  no signature append; no superseded-draft guard.
- Docs editing = raw `batchUpdate` (`update_doc`); Drive has create/copy but **no
  move/rename/share**; no restore points, suggestion modes, checkbox states, invite-state,
  Forms, Activity, revisions.
- Thin triage signals (subject+snippet) vs mise's has_invite/unread_count/last_sender/
  attachment_names.
- `search_corpus` declares `pageSize`/`pageToken` **but its description says pagination is
  unsupported for cross-corpus search** — params accepted, semantics absent.
- Comments: `read_file_content(includeComments=true)` inlines comment threads
  (Docs/Slides/Sheets) — capability exists; fidelity vs mise's located `comments.md` untested.

## Strategy conclusion (as discussed 2026-08-01)

**Mise for depth, Google for reach, skills for the opinion.** Keep mise's architecture
untouched (its seams are precisely what Google didn't build). Use Google's MCP + our own
clients for surfaces mise can't reach (claude.ai web/mobile; Chat). Port mise's workflows
as tool-surface-detecting skills. Two ITV paths for the security conversation: allowlist
Anthropic's client (policy-only; server side is now Google's own service), or ITV-owned
client + custom connectors (`mit-workspace-mcp-server` project can host both clients).
