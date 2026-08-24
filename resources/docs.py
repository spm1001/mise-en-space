"""
Static documentation resources — the text behind mise://docs/* and the live
mise://gmail/labels directory.

Moved out of server.py (mise-jimohe, 2026-06-10): these eight resources were
~760 lines of docstring text, swamping the entry point. server.py calls
register_docs_resources(mcp) once at import time; the functions stay plain
and importable so tests can read the text without a server instance.

The parameterised mise://tools/{tool_name} resource does NOT live here — it
must register after all @mcp.tool() decorators have run, so it stays in
server.py next to the registry build (ordering is load-bearing).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer


def docs_overview() -> str:
    """Overview of mise-en-space MCP server."""
    return """# mise-en-space

Google Workspace MCP server with filesystem-first design.

## Tools (3 verbs)

| Tool | Purpose | Writes files? |
|------|---------|---------------|
| `search` | Find files/emails, deposit results to `.mise/` | Yes |
| `fetch` | Download content to `.mise/`, return path | Yes |
| `do` | Act on Workspace (create, move, edit, draft/reply emails) | Varies |

## Sous-Chef Philosophy

When you fetch a doc/sheet/slides, open comments are automatically included
in the deposit as `comments.md`. The sous-chef brings everything you need
without being asked.

## Workflow

1. **Search** to find what you need
2. **Fetch** to download and extract content (includes open comments)
3. Read content from filesystem with standard tools
4. **Do** actions — create, move, rename, edit

## Content Types

Supported: Google Docs, Sheets, Slides, Forms, Gmail threads, PDFs, Office files, video/audio

## Resources

- `mise://docs/overview` — This overview
- `mise://docs/search` — Search tool details
- `mise://docs/gmail-search` — Gmail search operator reference (is:, in:, from:, label:, etc.)
- `mise://gmail/labels` — Live label directory (user + system labels with IDs)
- `mise://docs/fetch` — Fetch tool details and supported types
- `mise://docs/do` — Do tool details (create, move, rename, edit, Gmail triage)
- `mise://docs/workspace` — Deposit folder structure
- `mise://docs/cross-source` — Cross-source search patterns (Drive↔Gmail linkage)
"""


def docs_search() -> str:
    """Detailed documentation for the search tool."""
    return """# search

Search across Drive and Gmail. Deposits results to file for token efficiency.

## Filesystem-First Pattern

Search results are written to `.mise/search--{query-slug}--{sources}--{timestamp}.json` — one file per call, never overwritten (numeric suffix on collision).
The tool returns the path and summary counts. Read the file for full results.

This pattern:
- Saves tokens (results don't bloat context)
- Scales to many parallel searches
- Lets you decide what to examine

## Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | str | "" | Search terms. Optional when `type` or `folder_id` is set. |
| `sources` | list[str] | ['drive', 'gmail'] | Which sources to search (defaults to ['drive'] in guest mode, where the token has no Gmail scope) |
| `max_results` | int | 20 | Maximum results per source |
| `folder_id` | str | None | Drive folder ID to scope results to immediate children only. Non-recursive. Forces sources=['drive']. |
| `type` | str | None | Drive file type filter. Values: `folder`, `doc`, `spreadsheet`, `sheet`, `slides`, `presentation`, `pdf`, `image`, `video`, `form`. Applies to Drive only. |
| `time_min` | str | None | Calendar window start — ISO date or datetime. Requires 'calendar' in sources. |
| `time_max` | str | None | Calendar window end. A bare date runs to the END of that day. |

## Examples

```python
# Search both sources
search("Q4 planning")
# Returns: {"path": ".mise/search--q4-planning--drive-gmail--2026-01-31T21-12-53.json",
#           "drive_count": 15, "gmail_count": 8, ...}

# Filter by type (no keyword needed)
search(type="spreadsheet")
search("budget", type="spreadsheet")

# Scope to a specific folder (non-recursive — immediate children only)
search("GA4", folder_id="1UclqiqLBfe3BfLRNFTWb0eDbnssxA3Tp")
# Returns cues.scope note explaining non-recursive limitation

# Then read the file for full results
Read(".mise/search--q4-planning--drive-gmail--2026-01-31T21-12-53.json")
```

## Response Shape

```json
{
  "path": ".mise/search--q4-planning--drive-gmail--2026-01-31T21-12-53.json",
  "query": "Q4 planning",
  "sources": ["drive", "gmail"],
  "drive_count": 15,
  "gmail_count": 8,
  "cues": {
    "scope": "non-recursive — results limited to immediate children of folder '...'",
    "sources_note": "Gmail excluded — folder_id scopes to Drive only"
  }
}
```

`cues` is present when `folder_id` or `type` affects the search. `sources_note` when Gmail was excluded by `folder_id`. `type_note` when `type` was ignored (Drive not in sources).

## Deposited File Shape

The JSON file contains the full results:

```json
{
  "query": "Q4 planning",
  "sources": ["drive", "gmail"],
  "drive_results": [
    {"id": "...", "name": "...", "mimeType": "...", "modified": "...", "url": "..."}
  ],
  "gmail_results": [
    {"thread_id": "...", "subject": "...", "snippet": "...", "from": "...", "date": "..."}
  ]
}
```

## sources=['people'] — the staff directory

"Who is this colleague and who do they report to?" Read-only, and non-admin
despite the scope name — every request uses the Directory API's
`domain_public` view, available to any user on the domain.

Query grammar is the Admin SDK's, NOT Drive's:

| Query | Matches |
|---|---|
| `Neil Charles` | name and email — bare words do NOT match job titles |
| `email:jane.smith*` | address prefix |
| `orgDepartment:MIT` | everyone in a department (single-word value) |
| `orgTitle='Head of Strategy'` | any value with a SPACE — `=` and SINGLE quotes |

**The multi-word trap:** `orgTitle:Head of Strategy` and
`orgTitle:"Head of Strategy"` both return **zero with no error**, which reads
exactly like nobody holding that job. Use `=` and single quotes for anything
containing a space.

Results carry email, name, title, department, organization, location, manager.
A **single** hit is expanded — manager resolved to a name, direct reports
listed — at two extra calls; a multi-hit search returns flat profiles, so
narrow and look again.

Two honesty notes, also cued in responses: `manager` is the Workspace *account*
field rather than an HR record (at board level it can record who administers
the account), and colleagues can opt out of listing, so an empty result is not
proof a person does not exist.

## sources=['calendar'] — the diary window

"What is in the diary between 3 and 5 Aug?" needs no topic term:
`sources=['calendar']` alone lists the default ±7-day window, and
`time_min`/`time_max` set any explicit window — historical included
(backfilling event ids for old notes works). A bare date as `time_max`
covers its whole day, so `time_min='2026-08-03', time_max='2026-08-05'`
spans the 3rd, 4th AND 5th. Events OVERLAPPING the window are returned
(Google's semantics — right for clash-checking; an all-day event at the
edge can ride in on timezone skew). `query` still filters when given.

On overflow the two window kinds keep different survivors, cued in
`calendar_truncated`: the default now-centred window keeps events nearest
NOW (tomorrow's meeting must survive a busy week); an explicit window keeps
the chronological HEAD — advance `time_min` past the last event to page.

## Notes

- Drive search uses fullText contains (searches content, not just filename)
- Gmail search supports Gmail operators — see `mise://docs/gmail-search` for full reference
- Results are sorted by relevance (Google's ranking)
"""


def docs_gmail_search() -> str:
    """Gmail search operator reference — tested against production API."""
    return """# Gmail Search Operators

Gmail search accepts the same operators as the web UI. Pass these in the `query`
parameter of `search(sources=["gmail"], query="...")`.

Tested: 2026-01-31 against production Gmail API.

## Location & Status

| Operator | Example | What it finds |
|----------|---------|---------------|
| `in:inbox` | `in:inbox` | Inbox threads |
| `in:sent` | `in:sent` | Sent mail |
| `in:draft` | `in:draft` | Drafts |
| `in:anywhere` | `in:anywhere` | Including spam/trash |
| `is:unread` | `is:unread` | Unread messages |
| `is:read` | `is:read` | Read messages |
| `is:starred` | `is:starred` | Starred |
| `is:important` | `is:important` | Marked important |
| `is:snoozed` | `is:snoozed` | Snoozed |
| `label:X` | `label:work` | Custom or system label |
| `category:primary` | `category:primary` | Inbox tab |
| `category:updates` | `category:updates` | Updates tab |
| `category:promotions` | `category:promotions` | Promotions tab |

## People & Content

| Operator | Example | What it finds |
|----------|---------|---------------|
| `from:` | `from:alice@example.com` | Sender |
| `to:` | `to:team@company.com` | Recipient |
| `cc:` | `cc:manager@company.com` | CC'd |
| `subject:` | `subject:quarterly review` | Subject line |
| `"exact phrase"` | `"budget approved"` | Literal match |

## Attachments

| Operator | Example | What it finds |
|----------|---------|---------------|
| `has:attachment` | `has:attachment` | Any attachment |
| `has:drive` | `has:drive` | Drive file link |
| `has:document` | `has:document` | Google Doc attached |
| `has:spreadsheet` | `has:spreadsheet` | Google Sheet attached |
| `filename:` | `filename:report.pdf` | Attachment by name |
| `filename:*.xlsx` | `filename:*.xlsx` | Wildcard match |

## Dates

| Operator | Example | Notes |
|----------|---------|-------|
| `after:` | `after:2026/01/01` | Date format: YYYY/MM/DD (slashes, not dashes) |
| `before:` | `before:2026/03/31` | |
| `newer_than:` | `newer_than:7d` | Relative: d=days, w=weeks, m=months, y=years |
| `older_than:` | `older_than:30d` | |

## Size

| Operator | Example | Notes |
|----------|---------|-------|
| `larger:` | `larger:5M` | Units: K, M, G |
| `smaller:` | `smaller:1M` | |

## Boolean & Grouping

| Operator | Example | Notes |
|----------|---------|-------|
| `OR` | `budget OR forecast` | Either term (must be uppercase) |
| `-` | `-newsletter` | Exclude term |
| `()` | `(budget OR forecast) from:john` | Grouping |
| `AROUND N` | `AROUND 5 budget approved` | Words within N of each other |

## Triage Recipes

```
# Unread inbox
in:inbox is:unread

# Unread from a specific person this week
from:boss@company.com is:unread newer_than:7d

# Attachments needing review
has:attachment is:unread newer_than:30d

# Everything from a domain
from:@example.com

# Large emails (cleanup)
larger:10M older_than:1y
```

## Gotchas

- `resultSizeEstimate` from the API is unreliable — treat as "has results" signal, not count
- Gmail operators do NOT work in Drive search (Drive uses SQL-like syntax)
- Date format is `YYYY/MM/DD` with slashes — dashes will silently fail
- `in:anywhere` is needed to search spam/trash — default excludes them
"""


def gmail_labels() -> str:
    """Live label directory from the connected Gmail account."""
    from adapters.gmail import list_labels

    try:
        labels = list_labels()
    except Exception as e:
        return f"# Gmail Labels\n\nFailed to fetch labels: {e}"

    system = [l for l in labels if l["type"] == "system"]
    user = [l for l in labels if l["type"] == "user"]

    lines = ["# Gmail Labels", ""]
    if user:
        lines.append("## User Labels")
        lines.append("")
        lines.append("| Name | ID |")
        lines.append("|------|----|")
        for l in sorted(user, key=lambda x: x["name"]):
            lines.append(f"| {l['name']} | `{l['id']}` |")
        lines.append("")
    lines.append("## System Labels")
    lines.append("")
    lines.append("| Name | ID |")
    lines.append("|------|----|")
    for l in sorted(system, key=lambda x: x["name"]):
        lines.append(f"| {l['name']} | `{l['id']}` |")

    return "\n".join(lines)


def docs_fetch() -> str:
    """Detailed documentation for the fetch tool."""
    return """# fetch

Fetch content to filesystem. Writes to `.mise/` in current directory.

## Parameters

| Param | Type | Description |
|-------|------|-------------|
| `file_id` | str | Drive file ID or WHOLE URL (a `?gid`/`?tab`/`#heading`/`#slide`/`?disco` tail resolves to `cues.pointer` naming the deposited artefact; dangling pointers reported stale), Gmail thread ID or URL (search/label context rides as `cues.gmail_url_context`; a `#drafts/r…` link resolves to the draft's thread), an RFC 822 Message-ID, or Drive folder ID |
| `tabs` | list[str] | Tab names to fetch from a spreadsheet (default: all tabs) |
| `suggestions` | str | Google Docs suggested-edit view: `accepted` (default), `original`, `markup` |
| `recursive` | bool | Folder IDs only: full indented tree, depth 5 (default: immediate listing) |
| `raw` | bool | With `attachment=`: also deposit the untouched original bytes (PDF/Office originals are otherwise converted and discarded) |
| `thumbnails` | bool | Default True. False skips PDF page and Slides thumbnail rendering — much faster and lighter for text-only use (154s → 59s on a 256-page PDF) |

## Tab Filtering (Sheets)

For large multi-tab spreadsheets, use `tabs` to fetch only what you need:

```python
fetch("1spreadsheetId...", tabs=["Current", "Sky postcode database"])
```

Only named tabs are fetched from the API. Missing tab names produce a warning in cues.

## Suggested Edits (Docs)

When a Doc carries unresolved suggesting-mode edits, `suggestions=` picks the view:

- `accepted` (default) — suggestions applied: the suggester's intended text,
  suggested deletions honoured
- `original` — pre-suggestion text, all suggestions ignored
- `markup` — explicit spans: `{++inserted++}[s1]` / `{--deleted--}[s1]`;
  matching `[sN]` tags pair the delete+insert halves of one replace

Whenever suggestions exist, cues carry `has_suggestions: true`, `suggestion_count`,
`suggestions_mode`, and a warning — so a caller never acts on an ambiguous render
unknowingly. Suggestion-free docs cost one API call as before; the mode dance only
fires when suggestions are present.

## Supported Content Types

| Type | Output Format | Notes |
|------|---------------|-------|
| Google Docs | markdown + comments.md | Multi-tab support, inline images, open comments |
| Google Sheets | CSV + comments.md | All sheets, with headers, open comments |
| Google Slides | markdown + thumbnails + comments.md | Selective thumbnails, open comments |
| Google Forms | markdown + structure.json | Questions, sections, grids, quiz scoring |
| Gmail threads | markdown | Signature stripping, attachment list |
| **Drive folders** | **markdown** | **Directory listing: subfolders with IDs, files grouped by type** |
| PDFs | markdown | Hybrid: markitdown → Drive fallback |
| DOCX/XLSX/PPTX | markdown/CSV | Via Drive conversion |
| Video/Audio | markdown + AI summary | Requires chrome-debug for summaries |

## Automatic Comment Enrichment

For Google Docs, Sheets, and Slides, open (unresolved) comments are automatically
fetched and deposited as `comments.md` alongside the content. This follows the
sous-chef philosophy: bring everything the chef needs without being asked.

The deposit folder will contain:
- `content.md` (or `content.csv` for Sheets)
- `comments.md` (if there are open comments)
- `manifest.json` (includes `open_comment_count`)

## Large File Handling

Files over 50MB use streaming downloads to avoid memory issues.
- Download streams directly to temp file
- Content extracted from disk, not memory
- Temp files cleaned up after extraction

This supports gigabyte-scale Office files (common at ITV).

## Response Shape

```json
{
  "path": ".mise/doc--meeting-notes--abc123/",
  "content_file": ".mise/doc--meeting-notes--abc123/content.md",
  "format": "markdown",
  "type": "doc",
  "metadata": {"title": "Meeting Notes", "mimeType": "..."}
}
```

## Auto-detection

The tool auto-detects input type:
- Drive URLs (docs.google.com, sheets.google.com, slides.google.com, drive.google.com)
- Gmail URLs (mail.google.com)
- Gmail API IDs (16-character hex)
- Drive file IDs (default)

## Examples

```python
# Fetch by Google URL
fetch("https://docs.google.com/document/d/1abc.../edit")

# Fetch by ID
fetch("1abc...")

# Fetch Gmail thread
fetch("18f3a4b5c6d7e8f9")

# List folder contents (no search query needed)
fetch("1FolderIdHere...")
# Returns: subfolders with IDs (for further fetch/move), files grouped by type
```
"""


def docs_do() -> str:
    """Detailed documentation for the do tool."""
    return """# do

Act on Google Workspace — create, move, edit documents, and draft emails.

## Operations

| Operation | Description | Required params |
|-----------|-------------|-----------------|
| `create` | Create Doc/Sheet/plain file/folder from content, deposit, or file_path | `content`+`title` OR `source` OR `file_path`; folder: `title` only |
| `copy` | Duplicate file(s) into a folder — originals untouched | `file_id` (str or list), `folder_id` (optional), `title` (optional) |
| `move` | Move file to different folder | `file_id`, `folder_id` |
| `rename` | Rename a file in-place | `file_id`, `title` |
| `share` | Share file with people by email | `file_id`, `to` |
| `overwrite` | Replace full document content (Sheets: CSV content, `range=` aims a tab or cells; Forms: YAML/JSON spec replaces all questions) | `file_id`, plus `content` OR `source` OR `file_path` |
| `prepend` | Insert text at start of document | `file_id`, `content` |
| `append` | Insert text at end of document — or, with `tab='Title'`, place it in a NEW Google Doc tab | `file_id`, `content` |
| `replace_text` | Find and replace text in document (Sheets: across all tabs' cells, formulas untouched) | `file_id`, `find`, `content` |
| `draft` | Create Gmail draft (does NOT send) — or update an existing draft in place | create: `to`, `subject`, `content`; update: `file_id` (draft ID), `content` |
| `reply_draft` | Create threaded reply draft | `file_id` (thread ID), `content` |
| `archive` | Remove thread(s) from Inbox | `file_id` (thread ID or list) |
| `star` | Star thread(s) | `file_id` (thread ID or list) |
| `label` | Add/remove a label on thread(s) | `file_id` (thread ID or list), `label` |
| `comment` | Open a NEW comment thread on a Drive file | `file_id`, `content` |
| `comment_reply` | Reply to / resolve / reopen a Drive file comment | `file_id`, `comment_id`, plus `content` and/or `action` |
| `trash` | Trash Drive file(s) / discard Gmail draft(s) | `file_id` (single or list; routed by ID shape) |
| `respond` | Accept/decline/tentative a calendar invite | `file_id` (invite thread ID or event ID), `action` |
| `create_event` | Book a calendar event (attendees, Meet, recurrence, Drive attachments) | `title`, `time_min`, `time_max`; attendees ⇒ confirm gate |
| `update_event` | Edit an event — description/title/location direct, time/attendees/recurrence gated | `file_id` (event ID or invite thread ID) |
| `freebusy` | Availability for a set of people + common free slots + office days | `attendees`, `time_min`, `time_max` |
| `setup_oauth` | Bootstrap Google credentials (opens browser) | none (`force=true` to re-auth over existing token) |

**Overwrite** destroys existing content (images, tables, formatting). Use `prepend`/`append`/`replace_text` when existing content matters. On a **Form**, `content` is the same YAML/JSON spec as `create` (title, description, questions) — the edit loop is fetch (structure.json shows current state) → tweak the spec → overwrite. Replaces ALL questions wholesale; if the form already has responses their linkage to old questions is lost, so edit response-bearing forms in the Forms UI. On a **Sheet**, `range=` aims the write (A1 notation, mise-vadoko), three grains: a bare tab name (`range="Costs"`) clears and replaces that whole tab; a bounded range (`range="Costs!F9:F15"`) writes exactly those cells and touches nothing else; an anchor (`range="Costs!F9"`) writes the CSV's shape starting there, no clearing. Content is CSV either way, USER_ENTERED semantics — formulas parse, bare URLs auto-link, `=HYPERLINK("url","label")` renders a labelled link. Cell values also carry link syntax (mise-bazuvo): `[label](url)` becomes a real rich-text link (several per cell work — the multi-artefact index row), and a whole cell of `@url` becomes a smart chip. A chip REPLACES the cell text with the target's live title (rename-proof, icon-bearing) — which is why chips are explicit opt-in and a bare URL stays a URL. **Google Docs take chips too** (mise-rafote): in `create` (doc_type='doc') and `overwrite` on a Doc, a line consisting solely of `@url` becomes a chip — same opt-in grain, whole line instead of whole cell; mid-prose `@url` stays text. Workspace URLs only (the API rejects anything else), and the title is always server-enriched from the live resource — the API refuses a supplied one. `cues.chips_inserted` counts successes; a failed pass restores the literal `@url` text and reports `cues.chip_errors`. Without `range=`, a single-tab sheet gets its tab cleared and replaced (symmetric with create); a **multi-tab sheet refuses** and lists its tabs, because an aimless wholesale write on a shared multi-tab sheet is a footgun. An unknown tab in `range=` errors naming the available tabs. Other tabs are never touched.

**Markdown footnotes become real Docs footnotes** (mise-rubucu): in `create` (doc_type='doc') and `overwrite` on a Doc, `[^N]` anchors with matching `[^N]: definition` lines land as native footnotes (superscript reference + footnote pane) — Drive's import engine has no footnote concept, so mise strips the definitions pre-import and runs a Docs API pass after. Round-trips: fetch renders Docs footnotes back as `[^N]` + definitions (labels renumber — Docs footnotes are numbered). Code is exempt: a `[^N]` in a fence or inline code span is content, never an anchor. `cues.footnotes_inserted` counts successes; anything unprocessable (orphan anchor or definition, duplicate definition or anchor, table-cell anchors, an ambiguous anchor appearing twice post-import, pass failure) stays/returns as literal text and is named in `cues.footnote_errors` — definitions are never silently lost.

**Doc tabs** (mise-wisuzu): `append` with `tab='Title'` places `content` in a NEW tab of an existing Doc — the non-destructive home for a parallel redraft: existing tabs are never touched, and the result's `web_link` deep-links straight to the new tab (`?tab=<id>`). Tab content is **plain text** — markdown is not rendered, because Drive's markdown import cannot target a tab (measured 2026-08-24: aimed at a multi-tab doc it flattens the whole doc to one tab, destroying the rest — the same measurement is why `overwrite` REFUSES multi-tab Docs outright instead of silently destroying every tab but the first). A duplicate tab title warns rather than refuses (Docs allows it; the ids differ). Spreadsheet tabs are a different surface — `tab=` on a sheet refuses.

**Restore point (Google Docs)**: every mutating op on a Doc (overwrite/prepend/append/replace_text) captures the pre-edit head revision and returns `cues.restore_point {revision_id, modified_time}` — the exact File → Version history entry to revert to. `overwrite` additionally posts an `[agent]` comment in the doc naming that entry (a UI-visible marker); pass `restore_comment=False` to suppress it on shared docs where the notification would be noise. Capture is best-effort: if it fails you get a warning cue and the edit proceeds. Reverting is a human step in Version history (the API cannot restore or name versions). One exception: a `replace_text` matching **zero** occurrences returns no `restore_point` — nothing was written, so the anchor would point at the live document and read like proof of an edit. That call carries a `cues.warning` beginning `NO CHANGE` instead.

**Draft** creates a draft in Gmail's Drafts folder — user reviews and sends from Gmail. Drive file IDs in `include` are resolved to formatted links in the email body. The user's Gmail signature (from sendAs settings) is auto-appended to both MIME parts with links intact — do NOT write a sign-off in `content`; end at the last sentence of the message. **To update an existing draft in place**, pass `file_id` with the draft ID (from a previous draft/reply_draft result): `content` is required (body rebuilt, links + signature re-appended); `to`/`subject`/`cc` carry over from the existing draft when not resupplied; reply drafts keep their threading.

**Trash** routes by ID shape: Drive IDs go to the recoverable Drive trash (~30 days); Gmail draft IDs (`r` + digits) are discarded via drafts.delete — PERMANENT, drafts have no trash. Accepts a list for batch cleanup. Threads/messages are NOT trashable — use `archive`/`label`.

**Reply draft** fetches a thread, infers recipients from the last message, adds threading headers (In-Reply-To, References), and creates a draft in the correct conversation. Recipients auto-populated; use `reply_all=True` to Cc all original recipients. Auto-appends the Gmail signature like `draft` — no sign-off in `content`. **Superseded-draft guard:** if the thread already carries a draft, the call refuses and names it (Gmail renders only one draft inline per conversation, so a silent second draft hides where the user hits Send). Update the existing draft via `draft` with `file_id=<draft_id>`, or pass `supersede=True` to discard existing thread drafts and create fresh (permanent; rejected in remote mode).

**Share** is a two-step operation (confirm gate). First call returns a preview showing what would happen. Second call with `confirm=True` executes. This ensures the user approves before files become visible to others. Default role is `reader` (least privilege). Notification emails are suppressed.

**Archive/star/label** modify Gmail thread labels. Label names are resolved to IDs automatically (case-insensitive). Use `remove=True` with label to remove instead of add. All three accept `file_id` as a list for batch operations — returns per-thread results (like `move`).

**Comment** opens a NEW (unanchored) comment thread on a Drive file (Doc/Sheet/Slides) — the write-side twin of the `comments.md` you get on fetch. Use it to proactively flag something to a human in the doc's comment pane, when there's no existing thread to reply to. Content is auto-prefixed `[agent] ` and posts as *your* authenticated identity. Anchored comments (tied to specific text) aren't supported yet — the comment lands at the document level.

**Comment_reply** posts an in-thread reply to a Drive file comment (Doc/Sheet/Slides). Get `comment_id` from a fetched `comments.md` — each comment's header ends with `` · `comment_id` ``. Pass `content` to reply, `action='resolve'` (or `'reopen'`) to close/reopen the thread, or both (a bare resolve needs only `action`). Replies are auto-prefixed `[agent] ` so humans can tell agent replies from their own, and post as *your* authenticated identity — don't reply on a thread that's @-mentioned to a specific person as if you were them.

**Respond** RSVPs a meeting as the authenticated user — the organiser sees the response, exactly as if it were clicked in Calendar. `file_id` takes the invite's Gmail **thread id** (resolved to the live event through the invite's iCalUID, disclosed in `cues.resolved_from_thread`) or the Calendar **event id** directly. `action` is `accept`, `decline`, or `tentative`. Refuses on a cancelled meeting (an RSVP there changes nothing) and on events with no self attendee (your own event, or one you weren't invited to). The write is a read-modify-patch of the full attendees array — other attendees are never touched. A `permission_denied` here usually means the token predates the calendar.events scope (2026-08-09) — re-auth with `setup_oauth` `force=true`. Not available in remote mode.

**Create_event** books an event on the user's primary calendar. `time_min`/`time_max` are the start and end — ISO datetimes (naive ones mean wall-clock time in the user's own timezone, resolved from their diary) or two bare dates for an all-day event (end date exclusive, Google's rule). With `attendees`, the call is a two-step confirm gate: the first call returns a preview with a clash check against the user's own diary — show it, then call again with `confirm=True` to book and send invites in one go (invite-first: `send_updates` defaults to `all`; the invite IS the proposal). Without attendees it books directly. Extras: `content` = description, `location`, `meet=True` mints a fresh Meet link, `recurrence='RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU'` makes a series (a weekly BYDAY that skips the start's own weekday draws a warning — Google keeps the start as a stray extra instance), `include=[Drive file ids]` attaches files to the event, `properties={'mise:programme': '1to1-2026'}` adds queryable key-values (extendedProperties.private — UI-invisible; every mise-minted event also carries mise:minted_by/minted_at stamps automatically, so a reconciler can find programme events by key regardless of title edits), `color` sets the event colour by name or id (lavender/sage/grape/flamingo/banana/tangerine/peacock/graphite/blueberry/basil/tomato = 1–11; custom calendar LABELS are deliberately not surfaced — the palette needs a scope mise doesn't hold, and the write accepts unknown ids silently), `visibility='private'` hides the detail from colleagues who can see the diary, `transparency='free'` (or 'transparent') shows the event WITHOUT eating availability — the right setting for holds and focus blocks, since an opaque block turns up as busy in everyone's freebusy slot-mining. There is no delete op: a mis-booked event is removed in the Calendar UI, which is why the gate previews first.

**Update_event** edits an event — `file_id` is the Calendar event id or the invite's Gmail thread id (resolved like `respond`). Gate grain is blast radius: **structural** changes (both `time_min`+`time_max` to move it, `recurrence` — including converting a single event into a series, `attendees` which are always ADDED never removed, `meet=True`) preview first and email attendees on confirm (`send_updates` default `all`); **cosmetic** changes (`content` = replace description, `title`, `location`, `include` = add attachments, `properties` = add/overwrite queryable key-values — existing keys merge, never wiped, so it backfills stamps on pre-existing events, `color` = recolour by name or 1–11, `visibility`, `transparency` = busy/free) execute directly and quietly (`send_updates` default `none`). `cues.previous` carries the old values — events have no version history, so that cue is the undo reference. Guests can only add attendees (when the organiser allows it); everything else needs the organiser.

**Freebusy** answers "when can these people meet" as data: per-person busy blocks over `time_min`..`time_max`, plus — when `duration` (minutes) is given — computed common free slots (weekdays 09:00–17:30 in the user's timezone), plus each person's office days from their workingLocation events where their sharing allows. Two honesty cues to respect: people in `not_visible` are EXCLUDED from the slot arithmetic (their sharing hides even free/busy — a slot may clash with them), and "location not visible" means their sharing is free/busy-only, never "not in the office". Needs the calendar.freebusy scope (2026-08-19): older tokens 403 here with re-auth advice while every other calendar call still works.

**Setup_oauth** is the bootstrap path for users who haven't authenticated yet. It opens Google's consent screen in their default browser and runs a localhost callback listener; once they approve, the token is saved to macOS Keychain. Returns immediately with the auth URL inline (so the user can paste it manually if browser auto-open fails). If a token already exists, returns `status: already_authenticated`. Use `force=true` to re-auth (e.g. after revoking access). Only available in stdio mode — not exposed in remote mode.

## Parameters

### Drive operations

| Param | Type | Default | Used by |
|-------|------|---------|---------|
| `operation` | str | **required** | All |
| `content` | str | None | create, overwrite, prepend, append, replace_text, draft (email body), comment |
| `title` | str | None | create, rename, copy (rename the copy; single-file only) |
| `doc_type` | str | 'doc' | create ('doc', 'sheet', 'file', 'folder', 'form'). 'file' uploads as-is — MIME inferred from title extension. 'folder' creates an empty folder (no content needed). 'form' creates a Google Form from a YAML/JSON spec. |
| `folder_id` | str | None | create, move, copy (target folder — canonical name; optional for copy) |
| `file_id` | str | None | copy (str or list), move, rename, share, overwrite, prepend, append, replace_text, trash (str or list), draft (draft ID — update in place) |
| `destination_folder_id` | str | None | move (deprecated alias for `folder_id`) |
| `source` | str | None | create, overwrite (path to deposit folder) |
| `file_path` | str | None | create, overwrite (any readable local path — `/tmp`, `~/scratch` etc. all fine; no deposit needed) |
| `base_path` | str | None | Required with source or file_path (your cwd) |
| `page_setup` | str | None | create ('pageless' for pageless Google Docs) |
| `find` | str | None | replace_text (case-sensitive) |
| `tab` | str | None | append (Google Docs only — place `content` in a NEW tab of this title; plain text, existing tabs untouched) |
| `role` | str | 'reader' | share ('reader', 'writer', 'commenter') |
| `confirm` | bool | False | share (must be True to execute — first call previews) |
| `comment_id` | str | None | comment_reply (the comment thread to reply to — from `comments.md`) |
| `action` | str | None | comment_reply ('resolve' or 'reopen'; omit for a plain reply), respond ('accept', 'decline', 'tentative') |

### Email operations

| Param | Type | Default | Used by |
|-------|------|---------|---------|
| `to` | str | None | draft (recipient email), share (email to share with; comma-separated for multiple) |
| `subject` | str | None | draft (email subject line) |
| `cc` | str | None | draft, reply_draft (CC addresses, comma-separated; overrides inferred Cc for reply_draft) |
| `include` | list[str] | None | draft, reply_draft (Drive file IDs — resolved to formatted links in body) |
| `reply_all` | bool | False | reply_draft (if True, Cc all original recipients) |
| `label` | str | None | label (label name — resolved to Gmail label ID automatically) |
| `remove` | bool | False | label (if True, remove the label instead of adding it) |

### Calendar operations

| Param | Type | Default | Used by |
|-------|------|---------|---------|
| `attendees` | list[str] | None | create_event (invitees), update_event (ADDED to the event), freebusy (whose diaries; you are always included) |
| `time_min` / `time_max` | str | None | create_event + update_event (start/end — ISO datetime, or two bare dates for all-day), freebusy (the window) |
| `location` | str | None | create_event, update_event (free-text location) |
| `meet` | bool | False | create_event, update_event (mint a fresh Meet link) |
| `recurrence` | str or list[str] | None | create_event, update_event (RRULE/RDATE/EXDATE lines — on update, converts a single event to a series) |
| `include` | list[str] | None | create_event, update_event (Drive file IDs → event attachments) |
| `send_updates` | str | all (structural) / none (cosmetic) | create_event, update_event ('all', 'externalOnly', 'none' — who gets emailed) |
| `duration` | int | None | freebusy (minutes — triggers common-slot mining) |
| `properties` | dict[str,str] | None | create_event, update_event (queryable extendedProperties.private keys — no '=' in keys; update merges per-key) |
| `color` | str | None | create_event, update_event (event colour — name like 'tomato' or id '1'–'11') |
| `visibility` | str | None | create_event, update_event ('default', 'public', 'private') |
| `transparency` | str | None | create_event, update_event ('opaque'/'busy' blocks the slot; 'transparent'/'free' doesn't) |
| `confirm` | bool | False | create_event (with attendees), update_event (structural changes) — first call previews |

## Deposit-Then-Publish (source param)

Instead of passing content inline, write it to a `.mise/` deposit folder and pass the path:

```python
# 1. Claude writes content to disk (cheap)
# 2. Human inspects, edits if needed
# 3. Publish from deposit (15 tokens vs 5000 for inline CSV)
do(operation="create", source=".mise/sheet--q4-analysis--draft/", base_path="/path/to/project")
```

Title falls back to `manifest.json` title if not passed explicitly.
After creation, manifest.json is enriched with `status`, `file_id`, `web_link`, `created_at`.

## Response Shape (all operations)

All operations return a consistent shape:

```json
{
  "file_id": "1abc...",
  "title": "Document Title",
  "web_link": "https://docs.google.com/...",
  "operation": "move",
  "cues": { ... }
}
```

`cues` contains operation-specific context (e.g. `destination_folder` for move, `inserted_chars` for prepend, `occurrences_changed` for replace_text). Create also includes `"type": "doc"|"sheet"`.

Errors return `{"error": true, "kind": "invalid_input", "message": "..."}` with helpful validation messages.

## Examples

```python
# Create a document (inline content)
do(operation="create", content="# Meeting Notes\\n\\n- Item 1", title="Team Sync")

# Create from deposit folder (deposit-then-publish)
do(operation="create", source=".mise/sheet--q4-analysis--draft/", title="Q4 Analysis", doc_type="sheet", base_path="/path/to/project")

# Create from a local file (no deposit folder needed)
do(operation="create", file_path="report.md", title="Q4 Report", doc_type="doc", base_path="/path/to/project")

# Create a pageless doc (wide tables won't be clipped)
do(operation="create", content="# Wide Table\\n\\n| A | B | C | D | E |", title="Rosetta Stone", page_setup="pageless")

# Create a Google Form from YAML spec
do(operation="create", doc_type="form", content="title: Feedback\\ndescription: Please share your thoughts\\nquestions:\\n  - type: paragraph\\n    title: What went well?\\n    required: true\\n  - type: multiple_choice\\n    title: Rating\\n    options: [Excellent, Good, Fair, Poor]")

# Create a folder (no content needed)
do(operation="create", title="Research Data", doc_type="folder")

# Create a folder inside another folder (Shared Drives work too)
do(operation="create", title="Q4 Analysis", doc_type="folder", folder_id="1xyz...")

# Move a file to a different folder
do(operation="move", file_id="1abc...", folder_id="1xyz...")

# Rename a file
do(operation="rename", file_id="1abc...", title="Final Q4 Report")

# Share a file — step 1: preview (returns what would happen)
do(operation="share", file_id="1abc...", to="alice@example.com")
# → {"preview": true, "message": "Would share 'Report' with alice@example.com as reader", ...}

# Share a file — step 2: execute after user approves
do(operation="share", file_id="1abc...", to="alice@example.com", confirm=True)

# Share with multiple people as writer
do(operation="share", file_id="1abc...", to="alice@example.com, bob@example.com", role="writer", confirm=True)

# Overwrite document content (replaces everything)
do(operation="overwrite", file_id="1abc...", content="# New Content\\n\\nFresh start.")

# Overwrite from a local file (no deposit folder needed)
do(operation="overwrite", file_id="1abc...", file_path="updated-report.md", base_path="/path/to/project")

# Prepend text to start of document
do(operation="prepend", file_id="1abc...", content="# Important Update\\n\\n")

# Append text to end of document
do(operation="append", file_id="1abc...", content="\\n\\n---\\nLast updated: 2026-02-18")

# Find and replace text (case-sensitive)
do(operation="replace_text", file_id="1abc...", find="DRAFT", content="FINAL")

# --- Email drafts ---

# Compose a new email draft
do(operation="draft", to="alice@example.com", subject="Q4 Findings", content="Hi Alice,\\n\\nHere are the key findings from the Q4 analysis.")

# Draft with CC
do(operation="draft", to="alice@example.com", cc="bob@example.com", subject="Q4 Findings", content="Hi team,\\n\\nSee findings below.")

# Draft with Drive file links included in body
do(operation="draft", to="alice@example.com", subject="Q4 Analysis", content="Please review the attached documents.", include=["1abc...", "1xyz..."])

# --- Reply drafts (threaded) ---

# Reply to a thread (recipients auto-inferred from last message)
do(operation="reply_draft", file_id="thread_abc123", content="Thanks for the update. I'll review this today.")

# Reply-all (Cc inferred from all original recipients)
do(operation="reply_draft", file_id="thread_abc123", content="Good points. Let me follow up.", reply_all=True)

# Reply with Drive links
do(operation="reply_draft", file_id="thread_abc123", content="Here's the analysis you requested.", include=["1abc..."])

# --- Gmail organisation ---

# Archive a thread (remove from Inbox)
do(operation="archive", file_id="thread_abc123")

# Star a thread
do(operation="star", file_id="thread_abc123")

# Add a label (resolved by name)
do(operation="label", file_id="thread_abc123", label="Projects/Active")

# Remove a label
do(operation="label", file_id="thread_abc123", label="Follow-up", remove=True)

# --- Gmail triage via label ---
# The label operation handles system labels too — no separate operations needed.

# Mark as read (remove UNREAD label)
do(operation="label", file_id="thread_abc123", label="UNREAD", remove=True)

# Mark as unread (add UNREAD label)
do(operation="label", file_id="thread_abc123", label="UNREAD")

# Unstar (remove STARRED label)
do(operation="label", file_id="thread_abc123", label="STARRED", remove=True)

# To discover available labels: read the mise://gmail/labels resource

# --- Batch Gmail operations ---
# archive, star, and label all accept file_id as a list for batch triage.

# Archive multiple threads at once
do(operation="archive", file_id=["thread_1", "thread_2", "thread_3"])

# Batch mark as read
do(operation="label", file_id=["thread_1", "thread_2"], label="UNREAD", remove=True)

# Batch star
do(operation="star", file_id=["thread_1", "thread_2"])

# Batch returns: {"operation": "archive", "batch": true, "total": 3, "succeeded": 3, "failed": 0, "results": [...]}
```
"""

def docs_cross_source() -> str:
    """Documentation for cross-source search patterns."""
    return """# Cross-Source Search Patterns

When exploring context, you often need to bounce between sources:

- **Drive → Email**: Found a file, want the email thread that sent it
- **Email → Drive**: Found an email, want to read the attachments/linked files

## Direction 1: Drive → Email

### Pattern A: Search by filename

When you find a file in Drive, search Gmail for the email that shared it:

```python
# Found in Drive search
{"name": "xgbtest.R", "id": "abc123..."}

# Search Gmail for emails with that attachment
search("filename:xgbtest.R", sources=["gmail"])
```

The `filename:` operator searches attachment names.

### Pattern B: Files from "Email Attachments" folder

The user may have an exfiltration script that copies email attachments to Drive
for fulltext indexing. These files have email metadata in their description:

```
From: alice@example.com
Subject: Budget analysis
Date: 2026-01-15T10:30:00Z
Message ID: 18f4a5b6c7d8e9f0
Content Hash: abc123...
```

If you see a file in "Email Attachments" folder, the **Message ID** can be
used to fetch the source email thread:

```python
fetch("18f4a5b6c7d8e9f0")  # Returns the email thread
```

## Direction 2: Email → Drive

### Following attachments

When you fetch an email thread, attachments are listed in the markdown:

```markdown
**Attachments:**
- budget_v3.xlsx (application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, 1.2 MB)
- notes.pdf (application/pdf, 450 KB)
```

To find these in Drive (if exfiltrated):

```python
search("name contains 'budget_v3.xlsx'", sources=["drive"])
```

### Following Drive links

Emails often contain Drive links instead of attachments. These are also listed:

```markdown
**Linked files:**
- [1abc...](https://docs.google.com/document/d/1abc...)
```

Fetch directly by ID:

```python
fetch("1abc...")  # Works with file IDs from links
```

## The Exploration Loop

Context exploration often involves iterating:

1. Search Drive for topic → find file
2. Search Gmail `filename:X` → find email thread with context
3. Read email → discover new terms, people, related files
4. Search Drive with new terms → repeat

This loop discovers the **meaning** (in communications) behind **artifacts** (files).

## Gmail Search Operators

Useful operators for cross-source exploration:

| Operator | Example | Finds |
|----------|---------|-------|
| `filename:` | `filename:report.pdf` | Emails with attachment named report.pdf |
| `has:attachment` | `has:attachment budget` | Emails about budget with any attachment |
| `from:` | `from:alice@example.com` | Emails from Alice |
| `to:` | `to:team@company.com` | Emails to the team |
| `after:` | `after:2026/01/01` | Emails after Jan 1, 2026 |
| `before:` | `before:2026/02/01` | Emails before Feb 1, 2026 |
"""


def docs_workspace() -> str:
    """Documentation for the workspace/deposit folder structure."""
    return """# Workspace Deposit Structure

Fetched content goes to `.mise/` in the current working directory.

## Folder Structure

```
mise/
├── doc--meeting-notes--abc123/
│   ├── manifest.json
│   └── content.md
├── slides--q4-deck--xyz789/
│   ├── manifest.json
│   ├── content.md
│   ├── slide_01.png
│   ├── slide_02.png
│   └── ...
├── sheet--budget--def456/
│   ├── manifest.json
│   └── content.csv
└── gmail--re-project--thread123/
    ├── manifest.json
    └── content.md
```

## Folder Naming

`{type}--{title-slug}--{id-prefix}/`

- **type**: slides, doc, sheet, form, gmail, pdf, docx, xlsx, pptx, video
- **title-slug**: Slugified title, max 50 chars
- **id-prefix**: First 12 characters of resource ID

## manifest.json

Self-describing metadata for each deposit:

```json
{
  "type": "slides",
  "title": "Q4 Planning Deck",
  "id": "1OepZjuwi2emuHPAP...",
  "fetched_at": "2026-01-25T17:00:00+00:00",
  "slide_count": 43,
  "has_thumbnails": true,
  "thumbnail_count": 12
}
```

## Content Files

| Type | File | Format |
|------|------|--------|
| Docs, Slides, Forms, Gmail, PDF, Video | content.md | Markdown |
| Sheets, XLSX | content.csv | CSV |
| PPTX | content.txt | Plain text |

## Thumbnails

Slides get selective thumbnails — only fetched for:
- Charts (visual IS the content)
- Images (unless single large image = stock photo)
- Fragmented text (≥5 short pieces, layout matters)

Text-only slides and stock photos are skipped.
"""

def register_docs_resources(mcp: "MCPServer") -> None:
    """Attach the documentation resources to the server (called from server.py)."""
    mcp.resource("mise://docs/overview")(docs_overview)
    mcp.resource("mise://docs/search")(docs_search)
    mcp.resource("mise://docs/gmail-search")(docs_gmail_search)
    mcp.resource("mise://gmail/labels")(gmail_labels)
    mcp.resource("mise://docs/fetch")(docs_fetch)
    mcp.resource("mise://docs/do")(docs_do)
    mcp.resource("mise://docs/cross-source")(docs_cross_source)
    mcp.resource("mise://docs/workspace")(docs_workspace)
