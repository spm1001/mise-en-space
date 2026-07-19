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
    from mcp.server.fastmcp import FastMCP


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

Search results are written to `.mise/search--{query-slug}--{timestamp}.json`.
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

## Examples

```python
# Search both sources
search("Q4 planning")
# Returns: {"path": ".mise/search--q4-planning--2026-01-31T21-12-53.json",
#           "drive_count": 15, "gmail_count": 8, ...}

# Filter by type (no keyword needed)
search(type="spreadsheet")
search("budget", type="spreadsheet")

# Scope to a specific folder (non-recursive — immediate children only)
search("GA4", folder_id="1UclqiqLBfe3BfLRNFTWb0eDbnssxA3Tp")
# Returns cues.scope note explaining non-recursive limitation

# Then read the file for full results
Read(".mise/search--q4-planning--2026-01-31T21-12-53.json")
```

## Response Shape

```json
{
  "path": ".mise/search--q4-planning--2026-01-31T21-12-53.json",
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
| `file_id` | str | Drive file ID, Gmail thread ID, or Drive folder ID |
| `tabs` | list[str] | Tab names to fetch from a spreadsheet (default: all tabs) |

## Tab Filtering (Sheets)

For large multi-tab spreadsheets, use `tabs` to fetch only what you need:

```python
fetch("1spreadsheetId...", tabs=["Current", "Sky postcode database"])
```

Only named tabs are fetched from the API. Missing tab names produce a warning in cues.

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
| `move` | Move file to different folder | `file_id`, `folder_id` |
| `rename` | Rename a file in-place | `file_id`, `title` |
| `share` | Share file with people by email | `file_id`, `to` |
| `overwrite` | Replace full document content (Sheets: CSV content replaces the first tab) | `file_id`, plus `content` OR `source` OR `file_path` |
| `prepend` | Insert text at start of document | `file_id`, `content` |
| `append` | Insert text at end of document | `file_id`, `content` |
| `replace_text` | Find and replace text in document (Sheets: across all tabs' cells, formulas untouched) | `file_id`, `find`, `content` |
| `draft` | Create Gmail draft (does NOT send) | `to`, `subject`, `content` |
| `reply_draft` | Create threaded reply draft | `file_id` (thread ID), `content` |
| `archive` | Remove thread(s) from Inbox | `file_id` (thread ID or list) |
| `star` | Star thread(s) | `file_id` (thread ID or list) |
| `label` | Add/remove a label on thread(s) | `file_id` (thread ID or list), `label` |
| `comment` | Open a NEW comment thread on a Drive file | `file_id`, `content` |
| `comment_reply` | Reply to / resolve / reopen a Drive file comment | `file_id`, `comment_id`, plus `content` and/or `action` |
| `setup_oauth` | Bootstrap Google credentials (opens browser) | none (`force=true` to re-auth over existing token) |

**Overwrite** destroys existing content (images, tables, formatting). Use `prepend`/`append`/`replace_text` when existing content matters.

**Draft** creates a draft in Gmail's Drafts folder — user reviews and sends from Gmail. Drive file IDs in `include` are resolved to formatted links in the email body. The user's Gmail signature (from sendAs settings) is auto-appended to both MIME parts with links intact — do NOT write a sign-off in `content`; end at the last sentence of the message.

**Reply draft** fetches a thread, infers recipients from the last message, adds threading headers (In-Reply-To, References), and creates a draft in the correct conversation. Recipients auto-populated; use `reply_all=True` to Cc all original recipients. Auto-appends the Gmail signature like `draft` — no sign-off in `content`.

**Share** is a two-step operation (confirm gate). First call returns a preview showing what would happen. Second call with `confirm=True` executes. This ensures the user approves before files become visible to others. Default role is `reader` (least privilege). Notification emails are suppressed.

**Archive/star/label** modify Gmail thread labels. Label names are resolved to IDs automatically (case-insensitive). Use `remove=True` with label to remove instead of add. All three accept `file_id` as a list for batch operations — returns per-thread results (like `move`).

**Comment** opens a NEW (unanchored) comment thread on a Drive file (Doc/Sheet/Slides) — the write-side twin of the `comments.md` you get on fetch. Use it to proactively flag something to a human in the doc's comment pane, when there's no existing thread to reply to. Content is auto-prefixed `[agent] ` and posts as *your* authenticated identity. Anchored comments (tied to specific text) aren't supported yet — the comment lands at the document level.

**Comment_reply** posts an in-thread reply to a Drive file comment (Doc/Sheet/Slides). Get `comment_id` from a fetched `comments.md` — each comment's header ends with `` · `comment_id` ``. Pass `content` to reply, `action='resolve'` (or `'reopen'`) to close/reopen the thread, or both (a bare resolve needs only `action`). Replies are auto-prefixed `[agent] ` so humans can tell agent replies from their own, and post as *your* authenticated identity — don't reply on a thread that's @-mentioned to a specific person as if you were them.

**Setup_oauth** is the bootstrap path for users who haven't authenticated yet. It opens Google's consent screen in their default browser and runs a localhost callback listener; once they approve, the token is saved to macOS Keychain. Returns immediately with the auth URL inline (so the user can paste it manually if browser auto-open fails). If a token already exists, returns `status: already_authenticated`. Use `force=true` to re-auth (e.g. after revoking access). Only available in stdio mode — not exposed in remote mode.

## Parameters

### Drive operations

| Param | Type | Default | Used by |
|-------|------|---------|---------|
| `operation` | str | **required** | All |
| `content` | str | None | create, overwrite, prepend, append, replace_text, draft (email body), comment |
| `title` | str | None | create, rename |
| `doc_type` | str | 'doc' | create ('doc', 'sheet', 'slides', 'file', 'folder', 'form'). 'file' uploads as-is — MIME inferred from title extension. 'folder' creates an empty folder (no content needed). 'form' creates a Google Form from a YAML/JSON spec. |
| `folder_id` | str | None | create, move (target folder — canonical name) |
| `file_id` | str | None | move, rename, share, overwrite, prepend, append, replace_text |
| `destination_folder_id` | str | None | move (deprecated alias for `folder_id`) |
| `source` | str | None | create, overwrite (path to deposit folder) |
| `file_path` | str | None | create, overwrite (any readable local path — `/tmp`, `~/scratch` etc. all fine; no deposit needed) |
| `base_path` | str | None | Required with source or file_path (your cwd) |
| `page_setup` | str | None | create ('pageless' for pageless Google Docs) |
| `find` | str | None | replace_text (case-sensitive) |
| `role` | str | 'reader' | share ('reader', 'writer', 'commenter') |
| `confirm` | bool | False | share (must be True to execute — first call previews) |
| `comment_id` | str | None | comment_reply (the comment thread to reply to — from `comments.md`) |
| `action` | str | None | comment_reply ('resolve' or 'reopen'; omit for a plain reply) |

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

def register_docs_resources(mcp: "FastMCP") -> None:
    """Attach the documentation resources to the server (called from server.py)."""
    mcp.resource("mise://docs/overview")(docs_overview)
    mcp.resource("mise://docs/search")(docs_search)
    mcp.resource("mise://docs/gmail-search")(docs_gmail_search)
    mcp.resource("mise://gmail/labels")(gmail_labels)
    mcp.resource("mise://docs/fetch")(docs_fetch)
    mcp.resource("mise://docs/do")(docs_do)
    mcp.resource("mise://docs/cross-source")(docs_cross_source)
    mcp.resource("mise://docs/workspace")(docs_workspace)
