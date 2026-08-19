# CLAUDE.md

**mise-en-space** — Google Workspace MCP (Drive, Gmail) with mise-en-place philosophy: everything prepped, in its place, ready for Claude to cook with.

## Versioning & releasing (suite-managed)

mise ships as part of the **Batterie de Savoir** suite, which carries **one suite-wide version**. So:

- **Do NOT hand-bump `.claude-plugin/plugin.json` to release.** This repo's own `plugin.json` version is **local-dev-only** — the assembler stamps every published plugin to the suite version, overwriting it.
- **Release via `/batterie:publish`** from this working tree — it bumps the suite version centrally and ships the change (a 2-repo push: this repo + the central suite bump). Never hand-run the assemble.
- **mise is vendored full-source** (it's the MCP plugin) — so *any* source edit here, plus `CLAUDE.md` / `instructions.md` / `skills/` / `hooks/` / **`scripts/`**, is vendored content that must ride a suite bump (a publish) to ship, or the assembler quarantines the plugin. `docs/` / `.bon/` / `tests/` edits are free (excluded from the vendor) — **that free list is exhaustive, so read anything absent from it as shipped.** `scripts/` is named explicitly because it reads like local tooling and is not: the shipped plugin carries it, which is how a `capture_fixtures.py` that had been broken since March 2026 went on being distributed to every user for four and a half months (mise-sowepo).

Full picture: `spm1001/batterie-de-savoir` → `CLAUDE.md` "Versioning convention" + `.bon/understanding.md`.

### Identity flavours (mise vs mise-home) — distinct from build flavours

Mise ships as **two identity flavours** from this one source: `mise` (work — `mit-workspace-mcp-server` OAuth client, `@itv.com`) and `mise-home` (personal/family — `planetmodha-workspace-mcp` client, `@planetmodha.com`). This is a *different axis* from the full/slim build flavours (see Development → Build flavours). The home flavour is produced by `spm1001/batterie/transforms/make-mise-flavour.sh`, a **guarded substitution transform** that rewrites the identity strings in the vendored copy:

- the data-dir name, keychain service, and `rules/mise.md` filename in `hooks/*.sh`, plus the `mise-batterie-de-savoir` / `mise-oauth-token` constants in `oauth_config.py`;
- the `plugin.json` **`identity`** (`"ITV (itv.com)"`→`"Planet Modha (planetmodha)"`) and **`displayName`** fields — the base values live in the *source* `plugin.json` (drift-exempt from the ratchet), the transform overrides `identity` for home and a field-specific guard asserts the swap landed;
- the **skill's tool-prefix** `mcp__mise__`→`mcp__<name>__` (so the home skill's `allowed-tools` point at `mcp__mise-home__*`, not the work server) and a flavour marker front-loaded into the skill *description*;

and swaps `credentials.json`. **If you edit those identity strings in the hooks, `oauth_config.py`, the `plugin.json` identity/displayName, or the skill's `mcp__mise__` tool refs, know they get rewritten per-flavour and the transform's guard will fail the build on any un-rewritten `mcp__mise__` leftover or missing identity.** Full topology (three repos) + the coexistence-clarity work: `.bon/understanding.md` → "Identity flavours" — shipped suite 1.8.7.

**Each flavour's rules shard is REGENERATED EVERY SESSION START.** `hooks/ensure-mise.sh` writes `~/.claude/rules/${NAME}.md` — a static routing rule naming both flavours and their tool prefixes, plus a per-flavour `<!-- mise flavour: … -->` stamp, then this repo's `instructions.md` — via temp+mv, on every single session. So **hand-editing `~/.claude/rules/mise*.md` is a no-op that survives until the next session and no further, silently.** The tell for a generated shard is its mtime: it equals the current session's start time. Fix the shard here (`instructions.md`) or in the hook, never in `rules/`. The routing rule is deliberately *static* — identical bytes in both builds, so there is nothing per-flavour to derive and no substitution rule for the transform to keep in step; the flavour's `identity` field still drives the **no-token warning**, which must say which flavour is unauthed and which sibling is fine.

## Architecture

```
extractors/     Pure functions, no MCP awareness (testable without APIs)
adapters/       Thin Google API wrappers (easily mocked)
tools/          MCP tool definitions + dispatch/remote orchestration (the wiring layer)
workspace/      File deposit management (.mise/ in cwd)
resources/      MCP resource text (mise://docs/*) + tool-doc registry
mise_en_space/  The blessed LIBRARY door (mise-dareti): one class (Mise), search/fetch/do, credentials selected in the constructor — the wheel-consumer contract (glaneur, Garni)
server.py       FastMCP registration shim (stdio default, --remote for StreamableHTTP) — ≤500 lines, enforced
apps-script/    Google Apps Script for email attachment extraction (runs in Google, not Python)
docs/           Design documents and references
```

**Shared utilities (root level)** — infrastructure that multiple layers need but doesn't belong in any single layer:

| File | Purpose | Used by |
|------|---------|---------|
| `html_convert.py` | HTML↔markdown: HTML→markdown via markitdown (needs tempfile — why it's not in extractors); markdown→HTML via python-markdown (`markdown_to_html`, for email draft bodies); Gmail body-selection policy (`select_body_text`, `has_data_table` — see Key behaviors) | adapters, tools |
| `markdown_import.py` | Pre-import rewrite for Drive's markdown→Doc engine (`convert_fenced_blocks`) — fenced code blocks become per-line inline-code spans (see Gotchas) | tools |
| `filters.py` | Attachment filtering logic (`is_trivial_attachment`, `filter_attachments`) — holds two `lru_cache`s | adapters, tools |
| `validation.py` | ID/URL validation (`validate_drive_id`, `validate_gmail_id`, etc.) | tools, adapters |
| `retry.py` | Retry decorator with exponential backoff and jitter | adapters |
| `logging_config.py` | Structured logging setup (`logger`, `log_retry`) | everywhere |
| `cues_util.py` | Identity cues (`with_identity`, `current_user_email`) — Protocol-typed, deliberately no adapter import | tools, models |

**Caches, enumerated** (no single authority — know all three): manual client singletons in `adapters/http_client.py` (`get_http_client`/`get_sync_client`, one per mode), two `lru_cache`s in `filters.py`, and a manual metadata cache in `adapters/drive.py` (~L579).

**Key references, with their age on the label:** `docs/information-flow.md` (flow diagrams, timing data — last content commit **2026-02**) and `docs/decisions.md` (design decisions with rationale — last content commit **2026-04**). Both are *historical*: useful for how something came to be, not authoritative on how it is now. The current picture lives in `.bon/understanding.md` and this file, which are rewritten continuously. Treating a months-cold file as current is the exact failure that killed the proposal to split `understanding.md` — see its "This file stays one file" section.

**Layer rules:**
- Extractors NEVER import from adapters or tools (no I/O, no tempfile, no os)
- Adapters NEVER import from tools
- Adapters MAY import parsing utilities from extractors
- Adapters use `convert_*` names, not `extract_*` (extract_* reserved for pure extractors/)
- Tools wire adapters → extractors → workspace. The do() machinery (`DISPATCH`, `REQUIRED_PARAMS`, `run_operation`) lives in `tools/dispatch.py`; remote orchestration in `tools/remote.py`
- `mise_en_space/` is the facade tier ABOVE everything: it imports downward freely and NOTHING may import it (enforced — it sits in every other tier's forbidden set). It is deliberately thin re-exports plus identity selection; logic belongs in the layers. Identity state lives in `token_store.configure_identity()` (in-process, never env mutation), and the frozen `http_client` pays only the three seams ambient already paid
- server.py registers tools/resources and holds the thin @mcp.tool wrappers — nothing else (capped at 500 lines)
- Shared utilities live at root level — they sit BELOW the layers and never import upward (retry.py's `adapters.http_client` import is the one documented exception)
- ALL of the above is mechanically enforced by `tests/unit/test_architecture.py` (`LAYER_RULES` for directories, `FILE_RULES` for server.py + root utilities). When adding a module tier, extend the rules — unpoliced tiers are where mass accumulates (server.py hit 1,318 lines before mise-jimohe)
- **Module size is policed repo-wide since 2026-08-03, not just on server.py** (mise-nebewe). Every `*.py` under `extractors/`, `adapters/`, `tools/`, `workspace/`, `resources/` is capped at **500 lines**, discovered by glob so a new file is covered automatically. The eleven modules already over that when the rule landed are frozen at their then-size in `_LEGACY_SIZE_BASELINE` — a **ratchet, not an exemption**: they may only shrink, and a shrink of more than 50 lines fails the test until the number is lowered, so the debt cannot grow back. If this trips on code you're adding, move it to a sibling; raising a baseline entry is a deliberate act to argue for in the commit message. Splitting the eleven is separate work and deliberately not bundled here

### Adapter Specializations

| Adapter | Purpose |
|---------|---------|
| `drive.py` | File metadata, search, download, export, comments |
| `docs.py` | Google Docs API (multi-tab support) |
| `sheets.py` | Sheets API (batchGet for values) |
| `slides.py` | Slides API + thumbnail fetching |
| `gmail.py` | Gmail threads and messages |
| `gmail_ids.py` | Alternate Gmail identifiers → thread id (message ids, rfc822 Message-IDs) — fields-masked failure-path resolvers |
| `gmail_browser.py` | a-family (self-sent) URL resolution via a logged-in CDP Chrome — background tab, DOM harvest, fail-open |
| `activity.py` | Drive Activity API v2 |
| `calendar.py` | Calendar events with meeting context (attendees, attachments, Meet links); `get_event_by_ical_uid` for live invite-state (`showDeleted=true` load-bearing); `respond_to_event` for RSVP writes (full-attendees-array patch — Calendar patch semantics replace arrays wholesale); write side since mise-rijeco: `insert_event`/`patch_event` (supportsAttachments + conferenceDataVersion always sent — omitting either is accept-and-drop; insert auto-stamps `mise:minted_by`/`minted_at` into extendedProperties.private, and patch MERGES that map per-key while replacing arrays wholesale — both probed 2026-08-19, mise-gujiro), `freebusy_query` (needs calendar.freebusy, NOT covered by calendar.events), `list_status_events` (colleague calendars, ACL-gated), `resolve_calendar_timezone` (user tz read from their own diary — calendars.get is out of scope); `list_events(private_property='k=v')` is the reconciler's keyed query — recurring instances inherit the master's properties |
| `forms.py` | Google Forms API v1 (structure: questions, sections, options) |
| `charts.py` | Sheets chart export via temporary Slides embed (Sheets API has no direct export) |
| `cdp.py` | Chrome DevTools Protocol cookie access (for genai.py; graceful fallback) |
| `conversion.py` | **Shared** Drive upload→convert→export→delete pattern |
| `pdf.py` | PDF text extraction (pdftotext -layout primary → markitdown only when poppler absent → Drive OCR fallback; mise-mitoki). The flattened-table detector judges ONLY markitdown output — it is calibrated on markitdown's failure shapes and false-fires on -layout's aligned columns (27/133 census slices) |
| `pdf_info.py` | The poppler subprocess seam: `count_pdf_pages` (pdfinfo) + `run_pdftotext` |
| `pdf_render.py` | PDF page-thumbnail rendering (CoreGraphics on macOS, pdf2image/poppler on Linux) — split from pdf.py 2026-08-18 |
| `office.py` | Office file conversion (DOCX/XLSX/PPTX via Drive) |
| `image.py` | Image files (raster + SVG→PNG rendering) |
| `genai.py` | Video summaries via internal GenAI API (requires chrome-debug) |

## MCP Tool Surface (3 verbs)

| Tool | Purpose | Writes files? |
|------|---------|---------------|
| `search` | Find files/emails/activity/calendar events/**people**, return metadata + inline preview | No |
| `fetch` | Download content to `.mise/` in cwd, return path + cues | Yes |
| `do` | Act on Workspace — 22 ops (create, copy, move, rename, share, overwrite, prepend, append, replace_text, draft, reply_draft, archive, star, label, comment, comment_reply, trash, respond, create_event, update_event, freebusy, setup_oauth) | Varies |

**Key behaviors:**
- `search` returns metadata only — Claude triages before fetching
- `search` accepts `type=` for MIME filter: `folder`, `doc`, `spreadsheet`/`sheet`, `slides`, `pdf`, `image`, `video`, `form`. `query` is optional when `type`, `folder_id` or `raw_query` is set.
- `search` **paginates** to `max_results` (10-page / 1000-result guard). Until suite 1.24.0 it silently capped at 100 whatever you passed — `nextPageToken` wasn't in `SEARCH_RESULT_FIELDS`, so the API never sent it (mise-werevi). Two cues answer different questions: `drive_truncated` = *fetched vs matched* (exact — a surviving page token, so it tells 25-of-25 from 25-of-1292), `preview_partial` = *shown vs fetched*, on every source. Both can fire.
- `search` accepts `raw_query=` — Drive query language, unescaped, Drive-only, mutually exclusive with `query` (mise-decaza). Reaches `or`, `not`, `name contains`, `modifiedTime >`, `'x' in owners`; composes with `type`/`folder_id`; `trashed = false` still ANDed. Operator-shaped input in plain `query` is **refused** with a pointer, because it used to keyword-search the operator words and return plausible wrong files. Plain `query` is AND across words — one term the estate doesn't use returns zero.
- `search` accepts `sources=['people']` — the Workspace **staff directory**: role, department, location and the reporting line (mise-mahiho). Query grammar is the Admin SDK's, not Drive's: bare words match name and email **only** (a job title returns zero — the zero is cued with the working syntax), `orgDepartment:MIT` scopes by team, `email:jane.smith*` by address prefix, and **any value containing a space needs `=` with SINGLE quotes** — `orgTitle='Head of Strategy'` finds its man while both `orgTitle:Head of Strategy` and `orgTitle:"Head of Strategy"` return zero with no error. That silent zero is the trap: it is indistinguishable from nobody holding the job, and it cost this build one wrong "syntax unresolved" note before a three-line probe settled it. A **single** hit is expanded automatically — manager resolved from an address to a name, direct reports listed — at two extra calls, gated on `len==1` like the checkbox oracle. **This is a non-admin capability despite the scope name**: `adapters/people.py` sends `viewType=domain_public` on every request, which Google documents as available to any domain user, and `tests/unit/test_people.py` pins both that every call site sends it and that `admin_view` never appears in executable code. Measured on ITV 2026-08-10; the control is that the same call *without* `domain_public` returns 403 "Not Authorized" on the same token. Two honesty limits ride in cues: `manager` is the Workspace **account** field rather than an HR record (at board level it records who administers the account — ITV's Chairman reads as reporting to the Company Secretary), and colleagues can opt out of listing, so empty is not proof of absence.
- **`search` Gmail rows arrive with their senders placed** (mise-fajabe): every own-domain address on a row gets a `people` entry (name, title, department, **division**, manager), plus `people_relation` when two placed people are a reporting line. The preview renders one line — `Stefano Figoni — Measurement Product Manager, Commercial`. **Enriched at SEARCH, not fetch**, deliberately: triage is "which of these thirty do I read?", so placement delivered at fetch arrives after the decision it informs, and you'd pay a thread fetch per sender. Affordable because inbox senders repeat — `_PROFILE_CACHE` in `adapters/people.py` is process-lifetime, deduped, parallel, own-domain-only, and **caches negatives** so a stranger isn't re-asked all session (measured on the real ITV mailbox: 2.2s cold for 15 threads, 1.2s warm; a 250-thread sweep found 106 correspondents of which only 36 are in the directory at all). The user is never placed to themselves — excluded at *lookup* so the fallback to the thread originator still fires. A profile with no title renders nothing (shared mailboxes, service accounts). **A group address is routine, not an error**: `users.get` answers 400 "Type not supported: userKey" for `mit-group@itv.com` and friends, converted to NOT_FOUND with `is_group`. Data is generous, render is lean — the full profile rides the deposit, so tuning detail is a change to `_describe_sender` in models.py and nothing else.
- **`fetch` of a Gmail thread places its participants too** (mise-nelizu): `cues.people` carries full profiles for every own-domain participant across From+To+Cc+Bcc (same adapter cache as search, so a thread just found by search re-pays nothing for its senders), `cues.people_relations` names reporting lines across the WHOLE set — the user's own cached profile joins the arithmetic (never the `people` dict), so "Kate Waters is Sameer Modha's manager" arrives on a thread from his boss — and `cues.people_note` marks the unplaced rest as external-or-opted-out, not failed lookups. Set-wise relations are deliberately non-quadratic: direct manager-report pairs get a line each; a shared manager NOT in the set gets one grouped line ("A, B and C report to the same manager"). Logic in `tools/fetch/gmail_participants.py::participants_with_placement`; the frozen orchestrator paid zero net lines (`) | people_cues`).
- **`org_map.json` is hand-maintained tenant data, and the second ITV-keyed fact mise ships** (after `credentials.json`). It coarsens the directory's `department` into a division, because the raw field is unusable: measured across the complete ITV directory 2026-08-10, **11,556 entries and 650 distinct department strings**, dirty with it (`Post production` 142 / `Post Production` 127 are one team split by a capital P). A rule set over those strings maps only ~66% of headcount — the rest is house knowledge no API holds (`Potato` is a Studios label, `Quay Street` is where Corrie is made). But it stays *small* because you only place people who email you: 13 departments across a real 250-thread sweep, against 650 company-wide. **Domain-keyed**, so the mise-home flavour finds no entry for planetmodha and the file is inert there rather than needing a build-time swap. **It never guesses** — an unmapped department yields no division, because `Strategy, Policy & Regulation` is the corporate centre and a keyword rule would file it under Commercial, misplacing exactly the senior people worth placing correctly. Data-only by design (no code in the file) and **hand-edited between releases**: add the row when an unfamiliar colleague appears, and it ships with the next publish like any vendored file. `tests/unit/test_people.py::TestOrgMap` is its only validator — nothing else checks a typo.
- `search` gmail results carry `has_invite` (thread has a calendar invite — free, from the parts mask) so triage can spot meetings
- `search` accepts `time_min`/`time_max` (ISO date or datetime) for the **calendar** source — any explicit window, historical included, no query term needed; `sources=['calendar']` alone lists the ±7-day default (mise-riduka: clash-checking and event-id backfill were impossible while a query was mandatory). A bare-date `time_max` covers its whole day. Google returns events *overlapping* the window. Overflow selection differs by window kind and the cue says which: default window keeps nearest-now (mise-bidopi), explicit window keeps the chronological head (advance `time_min` to page). Refused beside sources lacking calendar, or with `folder_id`/`raw_query` — a window that scopes nothing would be accept-and-drop.
- `search` gmail results and `fetch` thread metadata carry `web_link` — a clickable Gmail web URL, the token transform's inverse run on the thread id (mise-hetaba). Withheld when the thread may be self-sent (every visible sender is the user, or identity unresolved): thread-a tokens are not derivable (mise-lerulo) and a minted link would open the wrong conversation. Search omits the field silently; fetch appends a warning naming the gap.
- `fetch` auto-detects ID type (Drive file ID vs Gmail thread ID)
- `fetch` of a Gmail invite adds `cues.invite_state` — the **live** Calendar state (status/my_response/current_start/cancelled_at) resolved by iCalUID, not the email's frozen snapshot; a cancelled meeting also emits a warning. Best-effort (skipped silently without calendar scope). See `docs/2026-07-07-meduto-invite-event-state.md`.
- `fetch` on a 16-hex Gmail id that 404s as a thread **resolves it as a MESSAGE id and refetches its thread** (`messages.get` → `threadId`, fields-masked, failure path only) — Gmail gives threads and messages one id shape, and a message id only resolves as a thread when that message *heads* the thread. The rescue is **always disclosed** in `cues.warnings`, naming both ids; it is never silent. If the id is neither, the error says so rather than re-suggesting the lookup that just failed. See mise-saroca.
- **A `fetch` 404 names the likely id type and the next move** — `diagnose_fetch_404()` in `validation.py` classifies four shapes (Gmail draft id, 16-hex message-vs-thread, un-converted Gmail web token, well-formed Drive id) and the router's funnel appends the advice *additively*, keeping Google's own text. Raw 404s were the only failure class with no recovery route (6 of 23 lifetime failures). See mise-tuveda.
- `fetch` accepts an **RFC 822 Message-ID** (bare or `<angle-bracketed>`, from Gmail's Show original view) — resolved internally via an exact-match `rfc822msgid:` search (`adapters/gmail_ids.py`), disclosed as a cue, never silently. The deterministic route into self-sent (thread-a) threads whose web tokens cannot be converted (mise-lerulo).
- `fetch` accepts a **Show-original URL** (`?view=om&permmsgid=msg-f:<decimal>`) — the decimal converts to the hex API MESSAGE id by the thread-f transform, and the message→thread machinery does the rest. `permmsgid=msg-a:…` (self-sent) cannot convert; its refusal says to copy the Message-ID off the page the URL opens.
- `fetch` of **mise's own draft link** (`…/mail/#drafts/<r-id>`, emitted by every draft/reply_draft result) resolves the draft to its holding thread via `drafts.get` (`adapters/gmail_ids.py::get_thread_id_for_draft`, fields-masked, pre-flight in the router) and fetches that thread — disclosed as a cue naming both ids and the `do(draft, file_id=…)` edit route. A dead draft (sent/discarded) gets a lifecycle-teaching NOT_FOUND, never a bare 404. `#drafts/FMfcgz…` (the UI's own shape) carries a thread token and takes the normal route (mise-jujoti).
- **Gmail URL provenance is cued, not dropped** (mise-jujoti): a `#search/<query>/<token>` or `#label/<name>/<token>` URL carries its query/label into `cues.gmail_url_context` (unquote_plus'd), and a non-zero `/u/N/` account index draws a warning cue — mise reads the account it is authed to, not the URL's. Pure parsing in `validation.py::extract_gmail_url_context`.
- **A self-sent (thread-a) fragment URL resolves via a logged-in CDP browser when one is available** (`adapters/gmail_browser.py`, mise-johata): open the permalink in a background tab, read `data-legacy-thread-id` from the rendered DOM, fetch that thread, disclose as a cue. Endpoint from `MISE_CDP_ENDPOINT`/`PASSE_CDP` env, then localhost 9223/9222. Fail-open everywhere — no browser, lapsed session (SSO-wall detection needs a persistent streak; a silent refresh bounces through accounts.google.com), or timeout all fall through to the refusal below. Two live traps recorded in the module: `/json/new` takes its URL raw after `?` (not `url=`) so navigation is done via `Page.navigate`; msg-a Show-original URLs are excluded (the UI renders an error page for those).
- **A self-sent (thread-a / msg-a) refusal attaches `candidates`** — ~10 recent `in:sent` threads (thread_id, subject, from, date) on the error payload, plus a confirm-or-say-so instruction. The rail against the 2026-07-31 silent-substitution incident (mise-lerulo). Fail-open: a failed candidates search never doubles the error.
- **Gmail body selection is plain-text-first with one exception** (mise-voteki): when a multipart message's HTML part holds a data grid — `has_data_table` in `html_convert.py`: a table with NO nested table and ≥2 rows each holding ≥2 text-bearing cells — the body comes from converted HTML instead, because composers' auto-generated plain alternatives flatten tables to bare lines of cell text (Outlook, live case thread 19fb9faca1565748). Both structural requirements are load-bearing: a first cut requiring only ≥2 rows × ≥2 cells swapped 31 of 40 real promotions/updates messages (Slack pings, Docs shares, newsletters — all nested layout wrappers); with both, that corpus measured 0 false positives and the true positive still fired. The swap is always disclosed in `cues.warnings`; it never fires in slim builds (tag-stripping fallback would lose the table too). Policy lives in `select_body_text()` — the adapter just calls it.
- **`fetch` of a decorated Drive URL points at the named location instead of throwing it away** (mise-dogape). `?gid`, `?tab`, `#heading`, `#slide`, `?disco` are parsed at the `detect_id_type` seam (now a 3-tuple — the old 2-tuple silently destroyed them one line into the fetch), resolved **post-fetch against the deposit alone** (manifest + comments.md, zero extra API calls), and surfaced as `cues.pointer` ("heading 'Team Inbox:' in tab 'Template' — content.md from line 340"). Deposits are byte-identical for decorated and bare fetches; resolution data rides the manifest unconditionally (`structure` for docs, `sheet_id` in `tabs` — now written for single-tab sheets too, `slides_index` for decks). A dangling pointer is reported STALE (firm for tabs — ids are immutable; gentle for headings — only read-only), never ignored. Resolution is lookup, never shape-matching (`'p'`, `'g3f5d00ed841_0_0'`, `'mig_slide_003'` are all real slide ids). Logic lives in `tools/fetch/decorations.py`.
- `fetch` accepts `raw=True` alongside `attachment=` — deposits the attachment's **original bytes** beside the extraction (mise-buzafo). PDFs and Office files are otherwise converted and the original discarded, so the document itself was unreachable; only images and plain text survived. Lands in `cues.files` automatically. Pairs with `do(create, doc_type='file', file_path=…)` to put a Gmail-only artefact into Drive. Rejected without `attachment=`, and in remote mode (binary can't ride back inline).
- `fetch` accepts optional `tabs` param (list of tab names) to fetch only specific tabs from spreadsheets
- `fetch` accepts `thumbnails=False` — skips PDF page and Slides thumbnail rendering on every path (Drive PDFs, Gmail PDF attachments, Slides), default unchanged (mise-giwawa). Measured: 154s → 59s and 77 MB → 0 of PNGs on a 256-page annual report; the wall-clock lever for text-only corpus hydration. Slides guard rides along: with rendering off, content slides are not misreported as `thumbnail_failures`.
- **PDF page-citation fidelity is measured, never inferred** (mise-wujoga): every PDF deposit carries `page_markers` (form feeds counted in content.md) and `pdf_pages` (poppler's count via `adapters/pdf_info.py`, None when poppler/pdf2image absent), and a warning cue fires when per-page citations can't be derived (0 markers on a multi-page PDF, partial markers = misalignment, or the Drive path on a build with no page count). Marker survival is per-PDF, not per-path — markitdown kept the two-page fixture's form feed and dropped all 255 of the BBC annual report's in the same environment — so gating on `extraction_method` is wrong on real documents; that design was falsified live before shipping.
- `fetch` accepts `suggestions=` for Google Docs carrying suggested edits: `accepted` (default — suggestions applied, the suggester's intended text, deletions honoured), `original` (pre-suggestion text), `markup` (`{++ins++}[s1]`/`{--del--}[s1]` CriticMarkup, shared `[sN]` = one replace). `cues.has_suggestions`/`suggestion_count`/`suggestions_mode` + a warning fire whenever suggestions exist. First call is always SUGGESTIONS_INLINE (countable); the preview modes cost a second `documents.get` only when suggestions are present (checkbox-oracle pattern). See mise-wofomu.
- `fetch` accepts `recursive=True` on folder IDs — returns full indented tree (max depth 5, 1000 items)
- `do` routes via `operation` param — `do(operation="create", ...)`
- `do(move)`, `do(archive)`, `do(star)`, `do(label)` accept `file_id` as `str | list[str]` for batch operations — returns per-thread/file summary with `succeeded`/`failed` counts
- **Calendar writes** (mise-rijeco, suite 1.68.0): `do(create_event)` books events — `time_min`/`time_max` as start/end (naive times get the user's diary-resolved timezone; recurring events always carry an IANA zone for DST), `attendees` ⇒ share-style preview with clash check then `confirm=True` sends (invite-first: `send_updates` defaults `all`), `recurrence` RRULE lines (BYDAY missing the start's weekday draws a stray-instance warning — probed), `include` = Drive attachments, `meet=True` fresh Meet link. `do(update_event)` gates by blast radius — structural (time/attendees/recurrence/meet) previews and emails; cosmetic (description/title/location/attachments) runs quiet with `cues.previous` as the only undo (events have no revisions). Attendees/attachments merge wholesale (patch replaces arrays). `do(freebusy)` = busy blocks + mined common slots (office hours, user tz) + workingLocation office days; `not_visible` people are EXCLUDED from slot arithmetic loudly. `properties={k:v}` on both event ops = queryable extendedProperties.private programme keys (mise-gujiro; every insert also auto-stamps `mise:minted_by`/`minted_at`; update merges per-key — the backfill route; keys refuse `=`, the filter's own separator). `color=` (name or 1–11) on both event ops = the classic palette; calendar LABELS are a probed negative (mise-kawegu, 2026-08-19: unknown eventLabelId accepted-and-ENRICHED to a UUID, palette enumeration scope-gated behind calendars.get 403) — don't re-surface without new evidence. No delete op — the preview gate is the rail. All three ambient-refused and remote-excluded.
- `do(respond)` RSVPs a calendar invite as the user — `file_id` is the invite's Gmail thread id (resolved to the live event via its iCalUID, newest invite in the thread wins, disclosed in cues) or the Calendar event id; `action` is `accept`/`decline`/`tentative`. Organiser-visible immediately (no draft step); refuses cancelled meetings and no-self-attendee events; remote-excluded. Needs the `calendar.events` scope (swapped for `calendar.readonly` 2026-08-09) — a stale token gets a teaching 403 naming `setup_oauth` (mise-gepiwe)
- `do(create)` and `do(overwrite)` accept `file_path` to read content directly from a local file — no deposit folder needed. For `doc_type='file'`, reads as binary; for `doc`/`sheet`, reads as UTF-8 text. Mutually exclusive with `content` and `source`.
- `do(create)` accepts `doc_type='folder'` — creates a Drive folder (title only, no content needed). `supportsAllDrives` is set automatically for Shared Drive compatibility.
- `do(create)` accepts `doc_type='form'` — creates a Google Form from a YAML or JSON spec. Uses Forms API v1 (not Drive), so `folder_id`, `source`, and `file_path` are ignored. The `content` param is the spec with `title`, `description`, and `questions` array. Supported question types: `paragraph`, `short_answer`, `checkboxes`, `multiple_choice`, `dropdown`, `scale`, `text`, `section_break`. Returns form edit URL and responder URL in cues.
- `do(create)` accepts `page_setup='pageless'` (doc_type='doc' only) — sets pageless mode via Docs API after creation.
- `do(create)` with `doc_type='doc'` auto-embeds local images: `![alt](local/path.png)` in markdown triggers post-creation Docs API injection. Requires brief public sharing of each image via Drive permissions — may be blocked by enterprise DLP policies. Check `cues.image_errors` for failures.
- `do(create)` on a doc and `do(overwrite)` on a Google Doc turn a **whole line of just `@url` into a real smart chip** (mise-rafote, `tools/doc_chips.py`): placeholder swapped pre-import, `insertRichLink` post-import with `richLinkProperties.uri` ONLY — the API **refuses a supplied title** ("should not specify a title") and enriches title/mimeType server-side from the live resource, the Docs-side twin of Sheets' accept-and-enrich chipRuns. Non-Workspace URLs are refused by the API ("The URL is invalid"), so the parser leaves them as literal text; mid-prose `@url` is never a chip. The batch is atomic — on failure every placeholder is restored to its literal `@url` text (no sentinel residue) and `cues.chip_errors` says so; success cues `chips_inserted`.
- `do(move)` accepts `file_id` as a list for batch moves — validates destination once, returns per-file summary. The target folder is `folder_id` (canonical, shared with `do(create)`); `destination_folder_id` is kept as a deprecated alias.
- `do(draft)` and `do(reply_draft)` **auto-append the user's real Gmail signature** (sendAs settings) to both MIME parts — HTML with links intact, plus a text rendering for text/plain. Do NOT write a sign-off in `content`; the signature arrives by itself (suite 1.13.0, wituwa).
- **Superseded-draft guard** (mise-sasivo): Gmail allows N draft objects per thread but its conversation view renders only ONE inline — a silently created second draft hides exactly where the user hits Send (probed live 2026-07-22; the Drafts list shows a row per object, hence "two red Drafts, one visible"). `reply_draft` therefore lists the thread's drafts first and REFUSES if one exists, naming its id + snippet — update it via `draft(file_id=...)` or pass `supersede=True` to discard-then-create. Check is fail-open with a warning cue (a listing hiccup never blocks the draft). Adapter gotcha: `drafts.list` under a fields mask returns a ZERO-LENGTH body (not `{}`) when there are no drafts — `list_thread_drafts` parses raw bytes. `supersede` is rejected in remote mode (drafts.delete is permanent).
- **Sheets editing** (lirugi; range-aimed since mise-vadoko, links/chips since mise-bazuvo): `do(overwrite)` on a spreadsheet takes CSV plus optional `range=` in A1 notation — bare tab name (`"Costs"`) clears and replaces that tab, bounded range (`"Costs!F9:F15"`) writes exactly those cells, anchor (`"Costs!F9"`) spills the CSV's shape from there; USER_ENTERED semantics (formulas parse, `=HYPERLINK` renders). Un-ranged: single-tab sheets get whole-tab replace (symmetric with create); **multi-tab sheets REFUSE and teach `range=`** — a deliberate contract change from the old first-tab-plus-warning behaviour. Cell values carry link syntax: `[label](url)` → real rich-text link via a `textFormatRuns` overlay pass (several per cell), whole-cell `@url` → smart chip via `chipRuns` (WRITABLE, probed 2026-08-08 — and it REPLACES the cell text with the target's live title, which is why chips are opt-in and bare URLs stay URLs). `do(replace_text)` does literal cell find/replace across ALL tabs (matchCase, formulas excluded, `occurrences_changed` cue). `prepend`/`append` on sheets still reject, naming those two alternatives.
- **Pre-edit restore point** (mise-cizuzi): every mutating do() op on a Google Doc (overwrite/prepend/append/replace_text) captures the head revision BEFORE editing and returns `cues.restore_point {revision_id, modified_time}` — the precise File → Version history entry to revert to. `overwrite` additionally posts a document-level `[agent]` comment naming that entry (UI-visible marker; `restore_comment=False` to suppress on shared docs where the notification is noise). Capture is best-effort — failure warns, never blocks the edit. Named versions and keepForever have NO API surface for native Docs (both silently ignored — probed 2026-07-22); revision content IS recoverable via per-revision exportLinks, so a future `do(restore)` is possible.
- **Comments included automatically** — open comments deposited as `comments.md`
- **Cues in every response** — `cues` block surfaces files, comment count, warnings, email context
- `base_path` is required on all tools in stdio mode — MCP servers run as separate processes, `Path.cwd()` is theirs not Claude's. In remote mode, `base_path` is optional (temp dir used automatically).

## Remote Mode

`server.py --remote` (or `MISE_REMOTE=1`) runs as a StreamableHTTP server on `/mcp` for Claude.ai custom connectors. Key differences from stdio:

| Aspect | stdio (default) | remote (`--remote`) |
|--------|----------------|---------------------|
| Transport | stdin/stdout | StreamableHTTP on `/mcp` |
| `do()` operations | All 22 | 6 safe ops: create, draft, reply_draft, archive, star, label |
| Content delivery | Filesystem deposits | Inline in JSON-RPC response (`content` + `comments` fields) |
| `base_path` | Required | Optional (temp dir) |
| Tool description | Full | Restricted (only safe ops + relevant params) |
| Health endpoint | N/A | `/health` returns `{"status": "ok"}` |

**Architecture:** `_REMOTE_MODE` is determined at module load time (before `@mcp.tool()` decorators run) so tool descriptions adapt. This is intentional — argparse validates in `__main__` but the value must be available earlier for the conditional `description=` parameter on `@mcp.tool()`. Don't move this to argparse without understanding why it's early.

**Operation gating:** `REMOTE_ALLOWED_OPS` in `tools/remote.py` (the gate itself fires in server.py's do() wrapper). Rejected ops get a generic "not available in remote mode" error listing only allowed ops — restricted op names are not leaked.

**Binary content:** Image fetches in remote mode return metadata and cues but no inline content (binary can't be text-encoded). A cue warning explains this.

## Error Handling

Errors are `MiseError` (in `models.py`) with `ErrorKind`: `AUTH_EXPIRED`, `NOT_FOUND`, `PERMISSION_DENIED`, `RATE_LIMITED`, `NETWORK_ERROR`, `INVALID_INPUT`, `EXTRACTION_FAILED`. Each includes `retryable` hint.

**The conversion contract is deliberately two-tier** (mise-ceroru — don't "fix" the second tier into the first):

- **API-facing adapters convert at the adapter layer.** `drive.py` and `gmail.py` (plus thinner docs/sheets/slides call sites) map Google's HTTP taxonomy to `MiseError` kinds — the status code carries meaning (403→PERMISSION_DENIED, 404→NOT_FOUND, 429→RATE_LIMITED) and a `retryable` hint the tools layer couldn't reconstruct from a bare exception.
- **Processing adapters raise bare.** The conversion family (office, pdf, conversion, image, charts, forms, activity, calendar, genai, cdp) raises `ValueError`/`Exception` or returns None: their failures are local processing errors whose message IS the diagnostic — a MiseError wrapper would add ceremony, not information.
- **Every tools-layer funnel converts uniformly, so nothing reaches an MCP response raw:** fetch (`tools/fetch/router.py` — MiseError→its kind, ValueError→invalid_input, Exception→unknown), search (`tools/search.py` — per-source catch into `errors[]`; one source failing doesn't block the others), do() (`run_operation` in `tools/dispatch.py` — never raises; an exception escaping a handler becomes kind INTERNAL, since handlers format their own errors), and server.py's wrapper as backstop.

New adapter rule: interprets Google API responses directly → convert with kinds; post-processes content → raise with a clear message and let the funnel catch.

## Warnings Pattern

Data models have `warnings: list[str]` fields. Extractors populate them during processing (mutation, not return tuple — preserves simple `str` return type). Exception: `extract_message_content()` returns `tuple[str, list[str]]` for per-message processing.

## File Deposit Structure

```
.mise/
├── slides--ami-deck-2026--1OepZjuwi2em/
│   ├── manifest.json       # Self-describing metadata
│   ├── content.md          # Extracted text/markdown
│   └── slide_01.png        # Thumbnails (selective)
├── doc--meeting-notes--abc123def/
│   ├── manifest.json
│   └── content.md
└── gmail--re-project-update--thread456/
    ├── manifest.json
    └── content.md
```

**Folder naming:** `{type}--{title-slug}--{id-prefix}/` (ID first 12 chars for readability).

## Design Decisions and Their Consequences

*Facts about this codebase are recoverable by reading it; grep beats any list we could write. What is **not** recoverable is the reasoning that made a fact deliberate. This section holds only consequences that are expensive to re-derive and cheap to get wrong — if an entry here could be answered by a one-minute grep, it belongs somewhere else or nowhere.*

**Deposits keep content out of context. `Read` is where context is spent.** In stdio mode `fetch` returns `path`, `content_file`, `format`, `type`, `metadata` and `cues` — `FetchResult.content` is `None` except in remote mode, where the client has no filesystem. So the fetch itself costs almost no context; the cost lands later, when the caller reads the deposit. Two things follow, and both have been got wrong in practice:

- **Depositing more is nearly free, so never narrow a fetch to save context.** The lever is what the caller is *pointed at*, not what is *written to disk*. When a URL names a tab, heading, slide or comment, the right move is to deposit everything as usual and make the cue name the artefact holding it (`content_<tab-slug>.csv`, a `comments.md` entry, a line offset into `content.md`). That delivers the saving with nothing withheld — so a caller who ignores the cue still has the whole document, and the silent-partial-answer failure cannot occur. A whole design argument was had on the wrong axis before this was noticed (mise-dogape, 2026-07-27).
- **Remote mode is the exception.** There `content` *is* inline, so payload size is a genuine cost — but only there.

**Three verbs is a budget, not minimalism.** `do()`'s description is hard-capped at 2048 characters and the API silently drops schema properties above it (see the MCP description ceiling in `.bon/understanding.md`). Every new operation name spends that budget three times over: description, dispatch table, and the model's reasoning about which verb to pick. This is why the standing rule is to check whether an existing op already covers a need with different parameters before minting a new one — it is a resource constraint, not an aesthetic.

**`mcp[cli]` is pinned `<2.0.0` on purpose.** The 2026-07-28 MCP spec (stateless core, MRTR, header routing, Tasks as an extension) will land in SDK 2.x. mise uses **none** of the deprecated surfaces — no sampling, elicitation, roots, MCP logging or session id — its remote path is parked, and its `tools/list` ordering is already deterministic. There is a twelve-month deprecation window and nothing measured to gain, so the ceiling is a deliberate hold. **Treat a Dependabot PR raising it to 2.x as a spec migration, not a dependency bump.** Full adjudication: bon `mise-veraja`.

## Gotchas

| Gotcha | Detail |
|--------|--------|
| **Overwrite uses Drive import** | Google Doc overwrite uses `files().update()` with `text/markdown` media type — same import engine as create. All markdown formatting (headings, bold, tables) renders automatically. No Docs API involved. |
| **Fenced code blocks are rewritten before Doc import** | Google's markdown import engine mangles ALL code blocks (``` fences, ~~~, language-tagged, 4-space indented): every whitespace-delimited token becomes its own code-styled run — per-word pills in the UI, per-word backtick spans on re-export. Inline code imports clean. So `convert_fenced_blocks` (markdown_import.py) rewrites each fenced block into per-line inline-code spans joined by backslash hard breaks before `do(create)`/`do(overwrite)` upload — imports as one tight monospace paragraph (`\v` line breaks, which the extractor renders back as per-line spans). prepend/append use raw `insertText` (no markdown import), so they're untouched. Probed live 2026-07-23 (mise-sejule). |
| **Gmail web IDs ≠ API IDs, and the token is the LAST fragment segment** | `FMfcgz...` web IDs need conversion: `thread-f:` converts, `thread-a:` (self-sent, ~2018+) does not — and `QgrcJHs...` tokens (search-view URLs) decode to the same a-family (probed 2026-08-07; the drafts API doesn't know the r-number either — negatives recorded on `_decode_gmail_web_token`). The token is the **last** `/`-separated fragment segment — `gmail_fragment_segments()` splits rather than regex-matching, because the old pattern captured the *second* segment and so refused every 3+ segment URL (`#search/<query>/FMfcgz…`) with the id sitting right there. `diagnose_gmail_url()` returns per-class teaching text — Chat link, draft link, self-sent thread-a (names the `rfc822msgid:` route), bare view — and both `extract_gmail_id()` and `detect_fetch_input_problem()` surface it. Widening the regex instead of splitting is the fix that silently re-breaks. See `validation.py` (mise-jujoti). |
| **No search snippets** | Drive API v3 has no `contentSnippet` field. `fullText` search finds files but doesn't explain why they matched. |
| **Pre-exfil detection** | User runs background extractor to Drive. Value is that Drive fullText indexes PDF *content*. Check "Email Attachments" folder. |
| **Overwrite destroys content** | `overwrite` is a full replacement — images, tables, formatting all lost. Use `prepend`/`append`/`replace_text` when existing content matters. Since 1.20.0 a pre-edit restore point is captured automatically (`cues.restore_point` + an `[agent]` comment naming the Version history entry) — recovery is possible via the UI, but don't lean on it as a licence to overwrite casually. On spreadsheets, `range=` scopes the blast radius (since 1.40.0): a bounded range clears nothing outside itself, a bare tab name clears only that tab, and un-ranged overwrite on a multi-tab sheet refuses outright. |
| **No purpose parameter** | This MCP always prepares for LLM consumption. No archival/editing modes. |
| **Image size skip vs format skip asymmetry** | `att.size > 4.5MB` no longer causes a pre-download skip — oversized images are downloaded and resized. Unsupported MIME types (not in `SUPPORTED_IMAGE_MIME_TYPES`) still skip pre-download. Reason: size is fixable by resizing; unsupported format is not. Don't restore the size check without also removing the resize logic. |
| **get_deposit_folder wipes on re-fetch** | Every call to `get_deposit_folder` deletes existing files in that folder before returning it. This prevents stale files from previous fetches. Do NOT call `get_deposit_folder` twice for the same folder mid-operation (e.g. inside a retry loop) — the second call will wipe files the first call's writes produced. |
| **A working-tree edit is NOT reachable from the MCP envelope — and restarting does not help** | The plugin spawns the server with `--project ${CLAUDE_PLUGIN_ROOT}`, i.e. `~/.claude/plugins/cache/batterie/mise/<version>/`. It runs the **published plugin**, never this repo. So a change here is invisible to `mcp__*` calls no matter how many times the session restarts — what's needed is a new *published version*, not a new session. **This row said "smoke-test new features in a fresh session" until 2026-08-03, which is false and cost a session real confusion.** That makes "smoke through the envelope, then publish" circular. Break it with `scripts/smoke_stdio.py`, which spawns `server.py` from the working tree over real stdio MCP — same FastMCP stack, same tool wrapper, same JSON on the wire, just not spawned by Claude Code. After publishing, re-verify against the *installed artefact* under the cache, not the working tree. Corollary worth knowing: a long-running session keeps whatever version it started with, so an old session can be many releases behind with no signal (bds-sawalu). |
| **Share requires confirm gate** | `do(operation="share")` without `confirm=True` returns a preview — the API won't execute. Call once to preview, show user, call again with `confirm=True`. Non-Google emails (iCloud, Outlook) automatically fall back to notification email (Google requires it); check `cues.notified` to see which recipients were notified. |
| **`_REMOTE_MODE` is early** | Set at module load, not in `__main__`. Required because `@mcp.tool(description=...)` fires at decoration time. Don't "clean up" by moving to argparse — breaks conditional tool descriptions. For containers, use `MISE_REMOTE=1` env var (not `--remote` flag) — `sys.argv` is fragile under process managers. |
| **Remote fetch retry risk** | `get_deposit_folder` wipes on re-call (see above). In remote mode, HTTP client retries or Kube probes can trigger double-wipe. Don't add automatic retry at the HTTP level for fetch operations. |
| **Remote is single-user** | One `token.json`, one `lru_cache(maxsize=1)` per service. Multi-tenancy would require per-request credential injection — architecturally significant. This is a confirmed design choice. |
| **`search` query is `""` not `None` when omitted** | `query` defaults to `""`. Empty string and absent query are indistinguishable inside `do_search` — both skip the `fullText` clause. If you add a source that needs to distinguish "no query given" from "empty query", use a sentinel (e.g. `query: str \| None = None` and check `is None`). Don't assume `""` means "give me everything" — the type/folder_id validation gate catches the all-empty case. |
| **Image embedding needs public sharing** | `do(create)` with local image refs uses Docs API `insertInlineImage`, which requires a publicly accessible URL. Images are uploaded to Drive, shared publicly for seconds, then permissions revoked and temp files deleted. Enterprise Workspace accounts with DLP policies may block the `permissions.create` call — images will be skipped with `cues.image_errors`, doc is still created. |
| **`file_path` is stdio-only (gated)** | `file_path` on `do(create)` and `do(overwrite)` reads the server's disk. Remote mode rejects it outright in server.py's do() wrapper (the boundary). In stdio it's deliberately unrestricted — any readable path works, including `/tmp` and `~/scratch` (mise-jebude: the old cwd-containment rail rejected natural staging spots while guarding nothing, since the same Claude can Read any file and pass `content=`). |
| **Checkbox tick-state is export-only** | Google Docs checkbox checked-state is NOT in the Docs API (`documents.get` returns identical bullet dicts for checked/unchecked). `adapters/docs.py::_apply_checkbox_states` fetches the `text/markdown` export as an oracle — a **2nd API call, only when a checkbox list is present** (`is_checkbox_list` gate) — parses `[ ]`/`[x]` in document order, and tags each paragraph. Count-mismatch → plain bullets + a warning cue (never a wrong tick). The `~~` on checked rows in the export is synthesised by Google's renderer from the checked bit, NOT `textStyle.strikethrough` — don't try to read it from the API. |
| **`comments.md` locates comments (docs only)** | On a Doc fetch, `_enrich_with_comments` passes the doc content to the comments extractor, which correlates each comment's anchor against the document tree — comments render in **document order** with a `↳` locator (nearest heading › sub-group), and heading/group-anchored comments are flagged `⚠` (they scope the whole section). Sheets/slides pass no content and keep the flat API-order render. Anchor text is HTML-unescaped; multi-line span anchors quote every line. |

## Development

```bash
uv sync --all-extras                                # Install deps (full build + dev tools)
uv run --extra extraction python server.py          # Run MCP server (stdio, full build)
uv run --extra extraction python server.py --remote # StreamableHTTP on :8000/mcp
uv run python server.py --help                      # CLI help
uv run --all-extras python -m pytest                # Run tests (suite ASSUMES the full build)
uv run --all-extras python -m pytest tests/unit     # Unit tests only (fast, mocked)
uv run --all-extras python -m mypy models.py extractors/ adapters/ validation.py workspace/
uv run --all-extras python scripts/smoke_stdio.py   # drive the WORKING TREE over real stdio MCP
```

Integration tests require `-m integration` flag and real credentials.

**`tests/bench/` is the model-qualification battery, not part of the test suite** (mise-rolira): a committed, re-runnable harness that takes new model strings — the deposit-format league, the consumer riders, three generations of scorer. It exists so the next model generation gets *measured* against the deposit-format policy (`docs/2026-08-18-deposit-format-policy.md`) rather than assumed; per-rig facts live in `~/notes/practices/model-profiles/`, findings and archived evidence in `~/notes/practices/mise/`. Costs real API money — run deliberately, never in CI.

**The count IS printed — it is just at the bottom of a long scroll.** `uv run --all-extras python
-m pytest` ends with `2514 passed, 100 deselected in 38.58s` (count as of 2026-08-18) as the last line of ~92, because
`addopts` in `pyproject.toml` carries `-q` (which suppresses the *per-test* lines, not the summary)
plus a ~85-line coverage table that pushes the summary off the top of a truncated view.
`-m 'not integration'` is baked in too, hence the 100 deselected.

**Unit tests run hermetically — a green may not use the machine's token.** `tests/unit/conftest.py`
autouse-points `MISE_TOKEN_PATH` at an absent file, so every unit test stands where CI stands
(no ambient credential, identity unresolved). This exists because a dev-token-dependent green kept
CI red for sixteen runs across three publishes, invisibly (2026-08-09→12, mise-wahane's close note).
Tests of the credential machinery itself (`test_token_store`, `test_http_client`, `test_search`'s
source defaults) state their env explicitly with module fixtures; follow that pattern, never
delete the conftest pin to make a test pass.

**This paragraph said the opposite until 2026-08-04, and the correction is the lesson.** It read
"pytest prints no `N passed` summary line here", told readers the run's evidence was its exit code,
and sent them to `python -m pytest --co -q --no-cov` to sum per-file counts by hand. That was false
when written: a session that had just been burned by `-v` cancelling against `-q` saw a screen with
no `PASSED` lines and generalised to a screen with no summary — then corrected the `-v` clause in
the very next sentence and left the wrong headline standing. **A correction covers what it names
and nothing adjacent**; re-run the thing the claim is about, not the thing your correction is about.

**The `-v` corollary is real and still holds:** `-v` does not restore per-test lines, because
`addopts`' `-q` and your `-v` cancel to zero verbosity. Use `-vv` — and validate it against a clean
tree *before* trusting a red, because the failure mode here is an **empty result with exit code 0**,
which reads exactly like "nothing went wrong" and just as easily like "nothing was checked".
The same screen is reachable from the opposite direction: adding your own `-q` stacks with
`addopts`' `-q` to `-qq`, which genuinely suppresses the `N passed` summary while exiting 0 — so a
tidy-looking `pytest -q` here loses the count entirely (walked into live, 2026-08-13, mise-ligopu).
Run the bare documented command when you need the number.

**`scripts/smoke_stdio.py` is how you exercise the MCP envelope before publishing.** Unit
tests can't reach the FastMCP registration, the `@mcp.tool` wrapper, schema coercion or the
shape of what actually crosses the wire — and the live `mcp__*` tools can't reach your working
tree (see the Gotchas row above). This spawns `server.py` from the repo and speaks real MCP to
it, closing that gap. Add a case when you change fetch's failure surface.

**mypy's standing errors are baselined and ratcheted — the number lives in
`tests/unit/test_mypy_baseline.py`, not here** (mise-bunuvu, closed 2026-08-10). The suite runs
the documented command and compares per-(file, error-code) counts against `MYPY_BASELINE`: a NEW
error fails CI naming the verbatim mypy line; a FIXED error fails until the entry is lowered, so
banked debt cannot grow back. Raising an entry is a deliberate act to argue in the commit message.
The bare mypy command still prints the standing errors (~20 as of 2026-08-10) — read them through
the baseline, never by hand-count. This paragraph used to carry the count itself and was the
item's best evidence against that: 16 when written (2026-08-03), 18 the next day, 20 within the
week — twice moving with the named files untouched (stub churn, unproven) and twice via shipped
code that sat uncounted for a day. Per-error reasons, including the `extractors/image.py`
stubs-only pair, are enrolled beside their numbers in the baseline.

### Build flavours (mise-hibere, 0.7.9)

Local extraction (`markitdown[pdf]`, `pdf2image`) lives in an optional `extraction`
extra, **not** core. Two flavours result:

- **Full** — dev/CI and the marketplace plugin (the plugin spawns `uv run --extra
  extraction`). markitdown as the PDF fallback, HTML→markdown, PDF page thumbnails.
- **Slim / embedded** — what Cornichon vendors (`vendor.sh` installs plain core, no
  extra). `markitdown` is absent, `html_convert.py` degrades to tag-stripping, and
  PDF thumbnails are skipped. Image fetch still works (`pillow` is core).

**PDF text is orthogonal to the flavour axis since mise-mitoki (2026-08-18):** the
primary is poppler's `pdftotext -layout`, a SYSTEM binary no pip extra can carry —
so a slim build on a host with poppler gets first-class PDF text, and a full build
on a host without it degrades to markitdown with a teaching warning (plus a one-shot
session-start advisory from `ensure-mise.sh`). Without either, Drive server-side
conversion remains the floor.

Two things follow: (1) **run the test suite with `--extra extraction`** (or
`--all-extras`) — PDF-extraction tests assume markitdown is present and fail in a
slim env; (2) the slim PDF→Drive fallback needs Drive **write** scope (it uploads to
convert) — fine for Cornichon, whose PDFs come from the user's own Drive.

### Call Log

Every MCP tool call is logged to `~/.local/share/mise/calls.jsonl` (5 MB rotation, 3 backups). Fields: `ts`, `tool`, `params`, `ok`, `error` (on failure), `result` (key summary fields). Useful for debugging ghost docs, bad params, or unexpected tool behaviour without adding print statements. **`params` is a summary that omits `base_path`** — an all-absent base_path in the log is the logger, not the callers (the 2026-08-01 usage review nearly minted that phantom; cwd distribution needs transcripts).

```bash
# Last 10 calls
tail -10 ~/.local/share/mise/calls.jsonl | python3 -c "import json,sys; [print(json.dumps(json.loads(l), indent=2)) for l in sys.stdin]"

# Failed calls only
grep '"ok": false' ~/.local/share/mise/calls.jsonl | tail -5
```

## OAuth

**In-app bootstrap (canonical):** `mise.do(operation="setup_oauth")` — opens a Mac browser at the consent screen, runs a detached subprocess listener on `localhost:3000`, saves the token to macOS Keychain via `save_token`. Returns immediately with the URL inline as a fallback. This is the path Cowork users hit; it's also the path the friendly error wrapper in `adapters/http_client.py` points at when the token is missing.

**CLI fallback:**
```bash
uv run python -m auth --auto              # Auto (opens browser, runs listener, saves token)
uv run python -m auth                     # Headless — prints URL, paste back via --code
uv run python -m auth --code URL_OR_CODE  # Exchange code from headless flow
```

**Headless / remote-desktop boxes (e.g. tube): use the `--code` path.** Mint with plain `uv run python -m auth` (print mode, no listener), open the URL in a browser signed into the **right** Google account — tube's xrdp browser is on planetmodha while this client is Internal to itv.com, so click from an itv-signed browser (e.g. the Mac) — then paste the redirect URL back via `--code`. `can_open_browser()` refuses to fire xdg-open under `XRDP_SESSION`; set `MISE_NO_BROWSER=1` on any box whose browser is signed into the wrong account (detection can't know account suitability). Concurrent auth flows are safe since jeton 1.4.0 — `.pkce_state.json` keys verifiers by the OAuth `state` param, so mints merge instead of clobbering; if you redeem a bare code while several flows are in flight, paste the full redirect URL instead (it carries `state`). A fresh token landing on disk is picked up by a running MCP server on its next call (`_refresh_or_reload`) — no restart needed after re-auth.

`credentials.json` (OAuth client config, not secret) ships with the repo. The OAuth client lives in ITV's `mit-workspace-mcp-server` GCP project with **User type: Internal** — any `@itv.com` Workspace account can authenticate without verification or a test-user list. Token auto-refreshes; `clear_service_cache` handles revoked refresh tokens. Maintainer can also fetch credentials from GCP Secret Manager as fallback.

Token storage: macOS Keychain (`mise-oauth-token`) is the source of truth. `~/.claude/plugins/data/mise-batterie-de-savoir/token.json` is the persistent fallback (auto-created since 2026-05). The plugin-staging-dir token path is ephemeral on Cowork and should never be relied on.

**Ambient mode (service accounts — mise-wasagu):** `MISE_CREDENTIALS=ambient` makes mise mint credentials via `google.auth.default()` — Cloud Run metadata server, workload identity, and `GOOGLE_APPLICATION_CREDENTIALS` all resolve through that one call (loader: `adapters/ambient.py`). Explicit opt-in ONLY, never a fallback — a missing token keeps teaching `setup_oauth`, and setting it beside `MISE_TOKEN_PATH` is an error, not a precedence. The scope tier is fixed per deployment (`MISE_SCOPES=readonly` for never-writing consumers) and is Drive-family only: search defaults to `['drive']` and refuses explicit gmail/people/calendar with the reason; `draft`/`reply_draft`/`archive`/`star`/`label`/`respond`/`setup_oauth` refuse via a dispatch gate — a service account has no mailbox or personal calendar. NB service accounts also own zero Drive storage (Google's 2025 enforcement): SA writes only land in Shared Drives (teaching error: mise-finupa).

## How to Add a New Content Type

1. **Adapter** — Create `adapters/{type}.py` with fetch function (API calls, returns data)
2. **Extractor** — Create `extractors/{type}.py` with pure extraction function (data in, markdown out)
3. **Wire in tools** — Add handler in `tools/fetch/` and route in `tools/fetch/router.py`
4. **Model** — Add data model in `models.py` if needed
5. **Fixture** — Add to `fixtures/{type}/`. **Build the smallest thing that exercises what you're testing, by hand** — realism matters less than minimalism, and a fixture you can read in one screen is one you can reason about when it fails. Worked examples: `fixtures/gmail/outlook_reply_url_dense_quote.txt` and its two siblings, each written for one specific signature-stripping bug. If you need a real Google response, fetch that one document ad hoc and sanitise with `scripts/sanitize_fixtures.py`. There is deliberately no bulk-capture script — `scripts/capture_fixtures.py` held a standing list of test-document IDs, broke on 2026-03-18 when the httpx migration deleted `adapters/services.py`, and was **never missed in the four and a half months before anyone noticed**, because every fixture added in that window was hand-built for a specific bug. Deleted 2026-08-04, `mise-sowepo`; recover with `git show 91d4222:scripts/capture_fixtures.py` if a real need appears
6. **Tests** — Unit test for extractor (fixture → expected output), adapter mock test

## How to Add a New do() Operation

1. **Implementation** — Create `tools/{op}.py` with `do_{op}()` that validates its own params (accepts `str | None`) and returns `DoResult` on success or error dict on failure
2. **Dispatch** — Add handler to `DISPATCH` dict and required params to `REQUIRED_PARAMS` in `tools/dispatch.py`
3. **Register** — Add name to `OPERATIONS` in `tools/__init__.py`
4. **Export** — Add `do_{op}` to `tools/__init__.py` imports and `__all__`
5. **Resource docs** — Update `docs_do()` in `resources/docs.py` with new operation
6. **Advertise it, and check the budget** — add the op to `DO_DESCRIPTION_FULL` in `tools/dispatch.py`, then **measure**: that description is hard-capped at **2048 chars**, and over the line the API drops schema properties *silently* (`do()` has 26). Adding `copy` took it to 2036 — twelve characters — and only a manual measurement caught it. `tests/unit/test_tool_description_budget.py` now fails below 100 chars of headroom; when it does, move detail into `mise://docs/*` rather than trimming meaning. Also update the op **count** in CLAUDE.md, README.md and the SKILL.md table — those three drift silently (see `.bon/understanding.md`)
7. **Decide remote mode deliberately** — `REMOTE_ALLOWED_OPS` in `tools/remote.py` is a whitelist audited Mar 2026. New ops are excluded by default; adding one is a security decision, not a wiring step. Assert the choice in a test either way
8. **Tests** — Unit test for the implementation + `test_dispatch.py` verifies OPERATIONS/DISPATCH sync automatically

## Field Reports

`docs/` contains field reports capturing real-world skill/tool gaps. Pattern: notice gap → write field report → fix → commit together.

## README skill table is generated

The Skills table in README.md (between `GENERATED:SKILLS` markers) is rendered from
`skills/*/SKILL.md` frontmatter — never hand-edit it. After adding, removing or renaming
a skill: `uv run --script ../batterie-de-savoir/scripts/render-skills.py .` from the repo
root. CI re-checks it on every push (fetching the canonical script from batterie-de-savoir
raw main), so a stale table fails the build. If a table one-liner reads badly, fix the
SKILL.md description (skill-forge), not the table.

**Notes room:** `~/notes/practices/mise/` — the thinking half (usage discipline, field gotchas, what not to trust). Read it when the question is how we *use* this rather than how it works. (bon-jezahi, 2026-08-16)
