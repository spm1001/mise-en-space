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
   Program enrolment** (application at developers.google.com/workspace/preview; acceptance
   pending as of writing). `tools/list` gating is inconsistent: free on workspacemcp
   pre-enablement, 403 on calendarmcp/docsmcp without their services enabled.
3. Note: preview Program Terms forbid shipping Pre-GA features in public applications.

## Bench state

**Mise side (measured, this box, `mise-bench-summary.json`):** composed drive+gmail+calendar
search via `do_search`, 4 estate queries × 2 runs: cold 1.8–3.4s, warm 1.3–1.7s, 20+20
results with truncation cues. Quality note against ourselves: **Gmail results are
recency-ordered, not relevance-ordered** — one fresh broad thread topped 3 of 4 queries.

**Google side (staged, blocked on gate 2):** driver is `2026-08-01-google-mcp-spike/gmcp.py`
(refreshes mise's token in-memory from the plugin-data token.json, never prints it;
`GMCP_BASE` env selects the server). Resume with:

```bash
python3 docs/2026-08-01-google-mcp-spike/gmcp.py call search_corpus '{"query": "ViewersLogic", "pageSize": 20}' 2
```

Planned legs once unblocked: (a) `search_corpus` vs mise composed search — latency +
ranking + truncation honesty (their fan-out is inside Google's network, so expect a lower
latency floor; ranking and honesty are the real questions); (b) `read_file_content`
(includeComments=true) vs mise `fetch` on the same commented Doc — extraction fidelity.

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
