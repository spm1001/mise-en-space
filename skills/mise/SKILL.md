---
name: mise
description: Orchestrates content fetching via mcp__mise__ tools. Load before using search/fetch/do — invoke first when you see 'search Drive', 'search Gmail', 'find docs about', 'fetch this document', 'research in Workspace', 'move this file', 'create a doc', 'triage my inbox', 'archive these', 'draft an email'. Covers research loops, Gmail triage with batch ops, email drafting, and result filtering the tools alone don't know. (user)
allowed-tools: [Bash, Read, "mcp__mise__*"]
---

# mise

Content fetching for Google Drive and Gmail — via the mise-en-space MCP.

## First Run (no token yet)

If the MCP server returns an auth error, the user needs to authenticate with Google. **Do this for them — don't ask them to type CLI commands.**

```python
mise.do(operation="setup_oauth")
```

This opens a browser at Google's consent screen on the user's Mac, runs a localhost listener, exchanges the auth code, and stashes the token in macOS Keychain. The MCP call returns immediately with the consent URL inline as a fallback (in case the browser didn't auto-open). Once the user sees "Authorization Successful" in the browser, retry the original mise call.

If `setup_oauth` itself fails (e.g. port 3000 in use), the error message will name the remediation. The CLI fallback (`uv run python -m auth --auto` from the mise-en-space repo) exists for users running mise outside Cowork/Desktop, but `setup_oauth` is the path to default to.

## Identity & multi-account

When multiple Workspace connectors are loaded in the same session — Cowork's native Drive/Calendar bound to one Google account, mise bound to another — the connector names alone don't say which is which. **Mise responses self-disclose: `cues._identity.email` shows the authenticated email on every response.** Read it, especially when the user has both a personal and a work Workspace identity active.

When in doubt about which account a question targets, prefer `mcp__plugin_mise_mise__*` (or whatever name your runtime gives mise's tools) over generic Drive/Gmail tools — mise's binding is explicit. If you've fetched data and the user reacts with "that's not the account I meant", check `cues._identity` in the response, then re-route or have them re-auth with the right account.

**Iron Law: Files are artifacts. Emails are meaning.**

A document tells you *what* was decided. The email thread tells you *why*, who pushed back, and what concerns remain.

## Always: Pass base_path

**MCP servers run as separate processes.** Without `base_path`, deposits land in the server's directory — not yours.

```python
# ALWAYS include base_path
search("Q4 planning", base_path="/Users/modha/Repos/my-project")
fetch("1abc...", base_path="/Users/modha/Repos/my-project")
```

**Deposit accumulation:** `.mise/` (hidden — dot-named on purpose) grows without bound during a session. Be aware during heavy research — 15+ deposits add up.

## The Three Tools

| Tool | Purpose | Output |
|------|---------|--------|
| `search` | Find files/emails/activity/calendar events | Path to deposited JSON + counts |
| `fetch` | Extract content to disk | Deposit folder: content.md, comments.md, manifest.json |
| `do` | Act on Workspace (create, move, rename, share, overwrite, edit, draft, archive, star, label, trash) | File ID + web URL + cues |

`fetch` auto-detects input: Drive file ID, Drive URL, or Gmail thread ID.

## After Every Fetch

**This checklist applies to all workflows — quick fetch, research, everything.**

The fetch response includes a `cues` block with decision-tree signals — check it BEFORE reading files:

```json
"cues": {
  "files": ["content.md", "comments.md", "manifest.json"],
  "open_comment_count": 3,
  "warnings": [],
  "content_length": 4280,
  "email_context": null,
  "participants": ["Rupa Jones", "Ella Collis"]  // Gmail only
}
```

1. **Read `cues` first** — it tells you what's in the deposit and what to act on
2. If `open_comment_count > 0` → read `comments.md` (the real discussion lives here)
3. If `email_context` is populated → the file was shared via email; consider fetching that thread
4. If `warnings` is non-empty → note extraction issues
5. Read `content.md`
6. For Gmail: check `cues.files` for `*.pdf.md` (extracted attachment text)
7. For Gmail invites: if `cues.invite_state` is present, it's the **live** Calendar state, not the email's frozen snapshot — `{status, my_response, current_start, cancelled_at}`. A `status: "cancelled"` (with a warning) means the meeting is off even though the email body still reads as a live invitation; `current_start` reflects any reschedule. Trust this over the ICS in the body.
8. For Google Docs: if `cues.has_suggestions` is true, the doc carries unresolved suggested edits (`suggestion_count` says how many, `suggestions_mode` says how they were treated). The default render is **accepted** — the suggester's intended text, with suggested deletions honoured. Don't treat that text as settled: the suggestions are still open in the Doc. See "Docs with suggested edits" under Workflow 1.

`manifest.json` is still on disk for scripts/jq, but `cues` surfaces the actionable signals so you don't need to read it separately.

See `references/deposit-structure.md` for folder layout and attachment patterns.

## Workflow 1: Quick Fetch

**When:** "Get me this doc" / "Fetch this URL" / "Read that email thread"

```python
fetch("1abc...", base_path="...")                          # Drive file
fetch("https://docs.google.com/...", base_path="...")      # Drive URL
fetch("18f3a4b...", base_path="...")                       # Gmail thread
fetch("thread_id", attachment="budget.xlsx", base_path="...")  # Single attachment
fetch("folder_id", base_path="...", recursive=True)        # Full folder tree (depth 5, 1000 items max)
```

**Gmail URL gotcha:** Browser URLs contain web-format IDs (`FMfcgz...`), not API IDs. The MCP converts automatically, but conversion fails for self-sent emails (~2018+). If fetch errors on a Gmail URL, ask the user for the thread ID.

**What fetch can't take:** Only Workspace content is fetchable — `mail.google.com/#search/...` URLs and non-Google URLs (GitHub, docs sites) will 404. Run the query through `search` instead. And the 12-char ID fragment in a deposit folder name (`doc--title--1jinlqdtqLpw`) is a prefix, not a fetchable ID — the full ID is in that folder's `manifest.json`.

### Docs with suggested edits (the mark-up loop)

When a human marks up a Doc in **suggesting mode**, fetch handles the suggestions
explicitly — `suggestions=` picks the view, and `cues.has_suggestions` +
`suggestion_count` fire whenever any exist (with a warning naming the mode):

```python
fetch("1doc...", base_path="...")                          # default: suggestions ACCEPTED (the author's intended text)
fetch("1doc...", suggestions="markup", base_path="...")    # see the edits: {++inserted++}[s1] / {--deleted--}[s1]
fetch("1doc...", suggestions="original", base_path="...")  # pre-suggestion text
```

- **`accepted` (default)** — what the suggester intends the doc to say; suggested
  deletions are gone from this render. The right view for folding feedback in.
- **`markup`** — CriticMarkup spans; matching `[sN]` tags pair the delete+insert
  halves of one replace. The right view when you need to discuss or selectively
  apply edits. (The Docs API doesn't say who made each suggestion.)
- **`original`** — the text as it was before any suggestions.

**The fold-back loop:** human suggests + comments in the Doc → fetch with the
default (their intended text) → fold changes into your working copy → reply via
`comment_reply` / apply edits with `do()`. The API can't *create* suggestions, so
your edits land as real edits — propose contentious wording in a comment instead,
and let the human apply or approve it.

Then follow the **After Every Fetch** checklist above.

## Workflow 2: Research

**When:** "Help me prepare for the Lantern meeting" / "What do we know about X?"

This is where the skill earns its keep. Don't just search→fetch→read. Follow the **exploration loop:**

```
1. Search Drive for topic → find files
2. Fetch most relevant → read content + comments
3. Check email_context in results → find the sending thread
4. Search Gmail filename:X or from:sender → get the email
5. Read email → discover new terms, people, context
6. Search again with new terms → expand understanding
```

**When to stop:** 2-3 iterations usually suffice. Stop when you understand the key decision-makers and their positions, or when new searches return familiar results. Don't exhaust every thread — the goal is understanding, not completeness.

**The loop discovers meaning (in communications) behind artifacts (files).**

See `references/exploration-loop.md` for a worked example.

## Workflow 3: Precision Search

**When:** "Find emails from Elizabeth about contracts" / "Search for the budget spreadsheet"

### Gmail: Use Operators, Not Keyword Soup

```python
# BAD — keyword soup returns noise
search("Elizabeth Smith Project Alpha data privacy contracts")

# GOOD — operators target precisely
search("from:elizabeth@example.com after:2025/12/01", sources=["gmail"])
search("filename:strawman from:legal@example.com", sources=["gmail"])
search("has:attachment subject:lantern after:2025/12/01", sources=["gmail"])
```

Key operators: `from:`, `to:`, `filename:`, `has:attachment`, `after:`, `before:`, `subject:`, `in:sent`

**Two asymmetries that waste sessions:**

- **"What did X send/share?" needs `(from:X OR to:X OR cc:X)`** — the thing X "shared" often arrives in a thread someone else started, with X on To: or Cc:. `from:X` alone structurally misses it.
- **Short free-text tokens (`PR`, `AI`, `KPI`) are brittle through the API** — the Gmail UI fuzzy-matches them; the API doesn't. If a short-token query returns suspiciously little, drop the token and narrow by participants + date instead, then open each of the 5–20 hits. Before reporting an email "not findable", confirm in the Gmail UI.

See `references/gmail-operators.md` for the full set and the search-asymmetry detail.

### Drive: Keywords Only (Different Syntax!)

Drive search uses plain keywords — **not Gmail operators.** `from:`, `is:starred`, `subject:` will return 400 errors on Drive.

```python
search("Q4 budget", sources=["drive"], base_path="...")     # Drive only
search("budget 2026", base_path="...")                       # Both sources

# Type filter — narrows Drive results by file type (query optional)
search(type="spreadsheet", base_path="...")                  # All spreadsheets
search("budget", type="spreadsheet", base_path="...")        # Budget spreadsheets only
search(type="folder", sources=["drive"], base_path="...")    # List folders
```

Type values: `folder`, `doc`, `spreadsheet` / `sheet`, `slides` / `presentation`, `pdf`, `image`, `video`, `form`. Type filter applies to Drive only — ignored for Gmail.

### Triage Large Results

When search returns 20+ results, don't read the full JSON. Filter first:

```bash
jq '.drive_results[:5] | .[] | {name, id}' .mise/search--*.json
jq '.drive_results[] | select(.name | test("framework"; "i"))' .mise/search--*.json
```

Rule of thumb: <10 results → just read. >15 → filter with jq first.

Gmail results carry a free `has_invite` flag (the thread contains a calendar invite). It costs no extra call, but it's only a flag — to know whether the meeting is still on, `fetch` the thread and read `cues.invite_state` (see "After Every Fetch"). Filter for invites with `jq '.gmail_results[] | select(.has_invite)'`.

See `references/filtering-results.md` for patterns.

### Search Sources

Default sources are `['drive', 'gmail']`. Two additional sources are available:

| Source | What it returns | When to use |
|--------|----------------|-------------|
| `activity` | Recent comment events from Drive Activity API | "What's been discussed recently?" / "Any comments on my files?" |
| `calendar` | Events ±7 days around now, filtered by your query | "Is my meeting with X still on?" / meeting context for Drive files |

```python
# Recent comment activity
search("project update", sources=["activity"], base_path="...")

# Is tomorrow's meeting still on? (query matches summary/description/attendees)
search("Gareth", sources=["calendar"], base_path="...")

# Calendar enrichment (adds meeting_context to Drive results)
search("Q4 report", sources=["drive", "calendar"], base_path="...")
```

**`activity`** returns comment events — who commented, on what, when. Actors show as "Unknown" (people/ID limitation); the content and file are accurate. The query is NOT applied to activity — it always returns recent events.

**`calendar`** is NOT in default sources (adds an API call). The query IS applied (free-text match on summary, description, attendees, location) over a ±7-day window around now — upcoming events are first-class, so "confirm tomorrow's meeting" works. If more events match than `max_results`, the ones **nearest to now** are kept and `cues.calendar_truncated` says so. When included alongside `drive`, matching calendar event attachments add `meeting_context` to Drive results — connecting a file to the meeting where it was discussed.

## Workflow 4: Do (Act on Workspace)

**When:** "Make a Google Doc from this" / "Move this file" / "Update that doc" / "Add a note to the meeting minutes"

### The Operations

| Operation | What it does | Key params |
|-----------|-------------|------------|
| `create` | New Doc/Sheet/Slides/plain file | `content`+`title` OR `source` |
| `move` | Move file(s) between folders — single or batch | `file_id` (str or list), `folder_id` |
| `rename` | Rename a file in-place | `file_id`, `title` |
| `share` | Share file with people (confirm gate) | `file_id`, `to`, `confirm=True` |
| `overwrite` | Replace full file content (Google Doc or plain file; Sheets: CSV content replaces the first tab; Forms: YAML/JSON spec replaces all questions) | `file_id`, `content` OR `source` |
| `prepend` | Insert at start of file | `file_id`, `content` |
| `append` | Insert at end of file | `file_id`, `content` |
| `replace_text` | Find-and-replace in file (Sheets: across cell values, all tabs) | `file_id`, `find`, `content` |
| `draft` | Compose a new Gmail draft — or update an existing one in place | `to`, `subject`, `content`, optional `include` (Drive file IDs); update: `file_id` (draft ID) + `content` |
| `reply_draft` | Reply draft in an existing thread | `file_id` (thread ID), `content`, optional `include` |
| `archive` | Remove thread(s) from Inbox | `file_id` (str or list) |
| `star` | Star thread(s) | `file_id` (str or list) |
| `label` | Add/remove label on thread(s) | `file_id` (str or list), `label`, optional `remove=True` |
| `comment` | Open a NEW comment thread on a doc | `file_id`, `content` |
| `comment_reply` | Reply to / resolve / reopen a doc comment | `file_id`, `comment_id`, `content` and/or `action` |
| `trash` | Trash Drive file(s) / discard Gmail draft(s) — routed by ID shape | `file_id` (str or list) |

### Choosing the Right Edit Operation

**All edit operations work on both Google Docs and plain files** (markdown, JSON, SVG, YAML, etc. stored in Drive). The tool auto-detects the file type and uses the right API — Docs API for Google Docs, Drive Files API for everything else. No extra flags needed.

**Overwrite destroys everything** — images, tables, formatting, all gone. It's a full replacement from markdown. Use it when you're publishing a complete new version of a document.

**Surgical edits preserve existing content.** Use `prepend`, `append`, or `replace_text` when the document has content worth keeping:

| Situation | Use |
|-----------|-----|
| Publishing a complete document from scratch | `overwrite` |
| Replacing a draft with a final version | `overwrite` |
| Adding meeting notes to an existing doc | `append` |
| Adding a header/disclaimer to a doc | `prepend` |
| Updating a specific section or value | `replace_text` |
| Doc has images, tables, or rich formatting | `prepend`/`append`/`replace_text` (never overwrite) |
| Editing a markdown/JSON/SVG file in Drive | Any edit operation (auto-routes to Drive Files API) |

**Binary files** (images, PDFs, etc.) reject text operations (`prepend`/`append`/`replace_text`) with a clear error. `overwrite` works on binary files (full byte replacement).

**Editing a Form:** `overwrite` on a form takes the same YAML/JSON spec as `create` — fetch the form first (`structure.json` shows current state), tweak the spec (e.g. add one option to a checkbox question), and overwrite. It replaces ALL questions wholesale; if the form already has responses, edit in the Forms UI instead.

**Updating a draft:** `draft` with `file_id` (the draft ID a previous draft/reply_draft returned) rewrites that draft instead of minting a stray new one. `content` is required; `to`/`subject`/`cc` carry over when not resupplied; reply drafts keep their threading. Superseded drafts and files: `trash` — Drive files go to the recoverable bin, drafts are discarded permanently.

### Writing & Replying to Comments

Comments are a two-way channel with the human. Two write operations:

- **`comment`** opens a NEW thread — use it to proactively flag something ("this figure looks stale", "confirmed against source") when there's no existing thread to answer. Unanchored: it lands at the document level, not tied to specific text.
- **`comment_reply`** answers an existing thread (and can resolve/reopen it).

```python
# Open a new comment thread on a doc (no comment_id needed)
do(operation="comment", file_id="1abc...",
   content="Checked these totals against the source sheet — row 14 is off by 2.")
```

Close the comment loop without leaving mise: `fetch` a Doc/Sheet/Slides, read `comments.md`, then reply in-thread with `comment_reply`. Each comment's header in `comments.md` ends with its id as a code-span — `` ### [Alice <a@x.com>] • 2026-01-15 · `AAAA1234` `` — and that `comment_id` is what you pass.

```python
# 1. Fetch the doc — comments.md is deposited automatically when open comments exist
fetch(file_id="1abc...", base_path="/path/to/project")

# 2. Reply to a comment thread (comment_id from comments.md)
do(operation="comment_reply", file_id="1abc...", comment_id="AAAA1234",
   content="Good catch — fixed in the latest revision.")

# 3. Reply AND resolve in one call
do(operation="comment_reply", file_id="1abc...", comment_id="AAAA1234",
   content="Done.", action="resolve")

# 4. Resolve with no reply (bare resolve), or reopen
do(operation="comment_reply", file_id="1abc...", comment_id="AAAA1234", action="resolve")
do(operation="comment_reply", file_id="1abc...", comment_id="AAAA1234", action="reopen")
```

**Anti-patterns:**
- **Don't impersonate.** Replies post as *your* authenticated identity (the token's user), and mise auto-prefixes `[agent] ` so humans can tell. Don't reply on a thread @-mentioned to a *specific* person as if you were them — let the human answer where they were asked by name.
- **Don't guess the `comment_id`.** It comes from `comments.md` (or a raw Drive comments list), never invented. A wrong id 404s.
- **Don't double-prefix.** mise adds `[agent] ` itself — write the plain reply text, not `[agent] ...`.
- **Comments only exist on Google-native files** (Docs/Sheets/Slides). Plain/binary Drive files have no comment threads.

### Create and Move

```python
# Create doc
do(operation="create", content="# Meeting Notes\n\n- Item 1", title="Team Sync")
do(operation="create", content=content, title="Report", doc_type="doc", folder_id="1xyz...")

# Create sheet (see Sheet Creation below for details)
do(operation="create", content="Name,Score\nAlice,95\nBob,87", title="Results", doc_type="sheet")

# Create plain file (no Google conversion — stays as-is in Drive)
do(operation="create", content="<svg>...</svg>", title="diagram.svg", doc_type="file")
do(operation="create", content="# Notes\n\nContent here", title="notes.md", doc_type="file")
do(operation="create", content='{"key": "value"}', title="config.json", doc_type="file")

# Move single file
do(operation="move", file_id="1abc...", folder_id="1xyz...")

# Batch move — validates destination once, returns per-file summary
do(operation="move", file_id=["1abc...", "1def...", "1ghi..."], folder_id="1xyz...")
# Returns: {batch: true, total: 3, succeeded: 2, failed: 1, results: [...]}
```

**Create:** Without `folder_id`, the doc lands in Drive root. Response includes `cues.folder` showing where it landed. Use `doc_type="file"` for plain files (markdown, SVG, JSON, YAML, etc.) — MIME type is inferred from the title extension. The file stays as-is in Drive, no conversion to Google format. Response includes `cues.plain_file` and `cues.mime_type`.

**Move:** Enforces single parent — removes all existing parents, adds destination. Response includes `cues.destination_folder` (name) and `cues.previous_parents`.

### Rename and Share

```python
# Rename
do(operation="rename", file_id="1abc...", title="Final Q4 Report")

# Share — TWO-STEP confirm gate
# Step 1: Preview (returns what would happen, does NOT share)
do(operation="share", file_id="1abc...", to="alice@example.com")
# → {"preview": true, "message": "Would share 'Report' with alice@example.com as reader", ...}

# Step 2: Execute after user approves
do(operation="share", file_id="1abc...", to="alice@example.com", confirm=True)

# Share with role and multiple people
do(operation="share", file_id="1abc...", to="alice@example.com, bob@example.com", role="writer", confirm=True)
```

**Share requires user approval.** The first call without `confirm=True` always returns a preview. Show it to the user and only call again with `confirm=True` after they approve. Roles: `reader` (default), `writer`, `commenter`.

**Non-Google accounts** (iCloud, Outlook, etc.): Google requires a notification email. The tool handles this automatically — check `cues.notified` to see which recipients got an invite email.

### Overwrite

```python
# Full replacement from inline markdown
do(operation="overwrite", file_id="1abc...", content="# Q4 Report\n\nRevised findings...", base_path="...")

# From a deposit folder (fetch → edit locally → publish back)
do(operation="overwrite", file_id="1abc...", source=".mise/doc--q4-report--1abc/", base_path="...")
```

For Google Docs: uses Drive's import engine — all markdown formatting (headings, bold, tables, lists) renders automatically. Response includes `cues.char_count`.

For plain files: content is uploaded as-is. Response includes `cues.plain_file: true` and `cues.mime_type`.

### Surgical Edits

```python
# Add to end of document
do(operation="append", file_id="1abc...", content="\n\n## 18 Feb Update\n\nNew findings...", base_path="...")

# Add to start of document
do(operation="prepend", file_id="1abc...", content="DRAFT — Do not circulate\n\n", base_path="...")

# Find and replace (case-sensitive, all occurrences)
do(operation="replace_text", file_id="1abc...", find="Q3", content="Q4", base_path="...")

# Delete matched text (replace with empty string)
do(operation="replace_text", file_id="1abc...", find="DRAFT — ", content="", base_path="...")
```

`replace_text` response includes `cues.occurrences_changed` — check it to confirm the replacement happened.

### Sheet Creation

Pass CSV as `content` with `doc_type="sheet"`. Google's Drive import handles type detection — it gets numbers, dates, currencies, booleans, and formulae right ~94% of the time. **Trust it.** Don't pre-format.

```python
# Simple data
do(operation="create", doc_type="sheet", title="Team Scores", base_path="...",
   content="Name,Score,Pass\nAlice,95,TRUE\nBob,87,TRUE\nCarol,62,FALSE")

# With formulae — cells starting with = are preserved
do(operation="create", doc_type="sheet", title="Budget", base_path="...",
   content="Item,Cost\nLicences,12000\nHosting,8500\nTotal,=SUM(B2:B3)")

# Values with commas need CSV quoting
do(operation="create", doc_type="sheet", title="Staff", base_path="...",
   content='Name,Department,Salary\nAlice,"Sales, Marketing","£65,000"\nBob,Engineering,"£52,000"')

# From a deposit folder (saves tokens — don't inline large CSVs)
do(operation="create", doc_type="sheet", source=".mise/sheet--budget--abc123/", base_path="...")
```

**CSV quoting rule:** If a value contains a comma, wrap it in double quotes (`"Sales, Marketing"`). This is standard CSV — applies to currency with thousands separators (`"£65,000"`) and multi-word categories.

**Deposit-then-publish** is the preferred pattern for large data. Write CSV to a deposit folder, then pass `source=` instead of `content=`. The tool reads `content.csv` from the folder and uses the manifest title. Multi-tab deposits (with `tabs` in manifest) are auto-detected and create multi-tab sheets.

#### What Google auto-detects well

| Type | Example CSV value | Detected as |
|------|-------------------|-------------|
| Numbers | `95`, `3.14`, `-200` | Number |
| UK currency | `£1,200.00`, `€50` | Currency |
| Percentages | `45%` | Percentage |
| Booleans | `TRUE`, `FALSE` | Boolean |
| Dates (ISO) | `2026-02-17` | Date |
| UK dates | `17/02/2026` | Date |
| Formulae | `=SUM(A1:A10)` | Formula |

#### What needs help

| Problem | Example | Fix |
|---------|---------|-----|
| Leading zeros stripped | `00412` (product ID) → `412` | Prefix with tick: `'00412` |
| USD not detected | `$50.00` → text | USD works if locale is US; UK locale treats as text. Use plain number + format after |
| US dates ambiguous | `02/03/2026` → 2 Mar or 3 Feb? | Use ISO: `2026-02-03` |
| Text-that-looks-numeric | Phone `07700900123` | Prefix with tick: `'07700900123` |

**The tick prefix** (`'`) tells Google Sheets "treat this as text, not a number." Write it directly in the CSV value — Google strips the tick from display but preserves the text type.

#### Anti-patterns

| Don't do this | Do this instead |
|---------------|-----------------|
| Strip `£` signs before CSV | Leave them — Google detects UK currency |
| Format numbers as strings (`"95"`) | Plain `95` — let Google type it |
| Inline 500-row CSV as `content` | Write to deposit, use `source=` |
| Build formulae with absolute values | Use `=SUM(B2:B10)` — formulae work |
| Manually pad columns with spaces | CSV handles alignment; Sheets renders it |
| Bare commas in values (`£65,000`) | Quote: `"£65,000"` — or CSV breaks |

## Gmail Attachments

PDFs and images are extracted eagerly. **Office files (DOCX/XLSX/PPTX) are skipped** during thread fetch (5-10s each). Extract on demand:

```python
fetch("thread_id", attachment="budget.xlsx", base_path="...")
```

See `references/deposit-structure.md` for the full attachment layout.

## Workflow 5: Folder Triage

**When:** "Organise this Drive folder" / "What's in this folder?" / "Move all the spreadsheets into one place"

Three-step loop: explore → understand → batch-move.

### Step 1: Find subfolders

```python
# Find subfolders inside a parent (query optional — type alone is enough)
search(type="folder", folder_id="<parent_id>", base_path="...")

# Or search by name if you don't have the parent ID
search("Q4 reports", type="folder", base_path="...")
```

### Step 2: Explore the tree

```python
# Recursive fetch — builds full indented tree, capped at depth 5 / 1000 items
fetch("<folder_id>", recursive=True, base_path="...")
```

The deposited `content.md` shows the full hierarchy with file IDs. Read it to understand what's where.

**If the tree is truncated** (`cues["truncated"] is True`): the cap was hit. Fetch individual subfolders separately to explore those branches:

```python
fetch("<subfolder_id>", recursive=True, base_path="...")  # repeat per branch
```

### Step 3: Batch move

```python
# Move multiple files in one call — validates destination once, per-file summary
do(
    operation="move",
    file_id=["<id1>", "<id2>", "<id3>"],
    folder_id="<dest_id>",
    base_path="..."
)
# Returns: {batch: true, total: 3, succeeded: 2, failed: 1, results: [...]}
```

Check `results` in the response — each entry has its own `ok`/`error`. A failed move on one file doesn't block the others.

### Anti-patterns

| Pattern | Problem | Fix |
|---------|---------|-----|
| Assume truncated tree is complete | Miss files in capped branches | Check `cues["truncated"]` and fetch sub-branches |
| Search with full-text query for type filter | Unnecessary, adds noise | `type="folder"` alone is enough with a `folder_id` |
| Move files one at a time | Slow, no batch summary | Pass `file_id` as a list |

## Workflow 6: Inbox Triage

**When:** "Help me triage my inbox" / "Archive everything from that newsletter" / "Star the important threads"

The triage workflow combines search, review, and batch actions. Pagination and batch ops work together — search surfaces the full picture, batch operations let you act on it efficiently.

### Step 1: Search with Gmail operators

Target what matters using operators rather than keywords:

```python
# Unread in inbox
search("is:unread in:inbox", sources=["gmail"], base_path="...")

# Recent unread from a person
search("is:unread from:alice@example.com newer_than:7d", sources=["gmail"], base_path="...")

# Newsletters and promotions (good candidates for bulk archive)
search("category:promotions newer_than:30d", sources=["gmail"], base_path="...")

# Custom label
search("label:project-alpha is:unread", sources=["gmail"], base_path="...")
```

**Results are capped at `max_results` (default 20).** Search paginates internally up to that cap, then stops — a 200-thread inbox searched with defaults returns 20 threads. When the cap is hit, the response carries `cues.gmail_truncated` (not `cues.truncated`) telling you more exist. For real triage, pass `max_results` explicitly (e.g. 100+) and check for `gmail_truncated` before believing you've seen everything — a silently-partial picture is how shallow triage happens.

### Step 2: Review and decide

Read the search results. Each thread shows subject, participants, date, and snippet (drawn from the **latest** message). Three fields answer "whose move is it?" without fetching the thread: `last_sender` (who spoke last — `from` is the thread *originator*, often a different person), `from_me` (the latest voice is yours; `null` means identity unresolved — don't read it as "theirs"), and `unread_count`. Decide which threads to act on — fetch individual threads if you need more context before deciding:

```python
fetch("thread_id", base_path="...")  # Read the full conversation
```

### Step 3: Act in batch

Pass a list of thread IDs to process multiple threads in one call:

```python
# Archive threads you've reviewed
do(operation="archive", file_id=["thread1", "thread2", "thread3"], base_path="...")

# Star threads that need follow-up
do(operation="star", file_id=["thread4", "thread5"], base_path="...")

# Label threads for a project
do(operation="label", file_id=["thread6", "thread7"], label="follow-up", base_path="...")
```

Each batch call returns a summary with `succeeded`/`failed` counts and per-thread results — a failed operation on one thread doesn't block the others.

### The `label` operation covers more than labels

`label` works with system labels, which means it handles several triage actions through one operation:

| Triage action | How |
|---------------|-----|
| Archive | `archive` (or `label` with `label="INBOX"`, `remove=True`) |
| Star | `star` (or `label` with `label="STARRED"`) |
| Unstar | `label` with `label="STARRED"`, `remove=True` |
| Mark read | `label` with `label="UNREAD"`, `remove=True` |
| Mark unread | `label` with `label="UNREAD"` |
| Add custom label | `label` with `label="your-label-name"` |
| Remove custom label | `label` with `label="your-label-name"`, `remove=True` |

Label names are resolved automatically — use human-readable names like `"follow-up"`, not Gmail's internal IDs.

### Drafting emails

Compose drafts for the user to review and send from Gmail:

```python
# New email
do(operation="draft", to="alice@example.com", subject="Q4 update", content="...", base_path="...")

# Reply in a thread
do(operation="reply_draft", file_id="thread_id", content="Thanks for the update...", base_path="...")

# Include Drive files as formatted links in the body
do(operation="draft", to="team@example.com", subject="Report ready",
   content="Here's the report", include=["drive_file_id"], base_path="...")
```

Draft-only — Claude composes, the user reviews and sends from Gmail. This is a safety boundary, not a limitation.

Both draft ops auto-append the user's Gmail signature (from their sendAs settings, links intact) to the body. **Don't write a sign-off in `content`** — no "Best regards, ..." — end at the last sentence; the real signature lands below it. The `signature` cue in the response confirms it was appended.

### Common mistakes

| Mistake | What happens | Better approach |
|---------|-------------|-----------------|
| Keyword soup for triage search | Noisy results, hard to batch-act | Use operators: `is:unread in:inbox newer_than:7d` |
| Archiving without reviewing | Important threads disappear | Fetch uncertain threads first, then batch the clear ones |
| One thread at a time | Slow, many tool calls | Pass `file_id` as a list for batch operations |
| Separate mark_read operation | Doesn't exist as its own op | Use `label` with `label="UNREAD"`, `remove=True` |
| Forgetting `sources=["gmail"]` | Searches Drive too, slower and noisier | Set `sources=["gmail"]` for inbox work |

## Error Handling

| Error | Meaning | What to do |
|-------|---------|------------|
| `AUTH_EXPIRED` | OAuth token stale | Call `mise.do(operation="setup_oauth")` to re-authenticate (see First Run above) |
| `NOT_FOUND` | File/thread doesn't exist | Verify the ID; file may have been deleted or moved |
| `PERMISSION_DENIED` | No access to resource | Tell user they need to request access |
| `RATE_LIMITED` | Hit API quota | Wait 30s and retry once |
| `EXTRACTION_FAILED` | Couldn't parse content | Report to user with the file type and error detail |

## Anti-Patterns

| Pattern | Problem | Fix |
|---------|---------|-----|
| Keyword soup in Gmail | Noisy, imprecise results | Use `from:`, `filename:`, `after:` operators |
| Gmail operators in Drive search | 400 error from API | Drive uses plain keywords, not `from:`/`is:` |
| `from:X` for "what did X share" | Misses threads where X is recipient | `(from:X OR to:X OR cc:X)` |
| Trusting short tokens (`PR`, `AI`) | API stricter than UI — false "not found" | Participants + date filters; confirm in Gmail UI |
| Skip comments.md | Miss the real discussion | Check after every doc/sheet/slides fetch |
| Ignore email_context | Miss the story behind the file | Follow the exploration loop |
| Read full search JSON | Token waste on 35 results | Filter with jq first |
| Stop after first search | Shallow understanding | Loop: new terms → new searches |
| Omit base_path | Deposits vanish into server directory | Always pass it |
| Overwrite a doc with images/tables | Content destroyed, not recoverable | Use `prepend`/`append`/`replace_text` |
| `replace_text` without checking cues | Silent no-op if text not found | Check `cues.occurrences_changed > 0` |
| Share with `confirm=True` without preview | Bypasses user approval | Always call without confirm first, show preview, then confirm |
| Archive/star one thread at a time | Slow — one tool call per thread | Pass `file_id` as a list for batch operations |
| Looking for a `mark_read` operation | Doesn't exist | Use `label` with `label="UNREAD"`, `remove=True` |

## Integration

**Composes with:**
- **todoist-gtd** — @Claude inbox items may request research; results inform outcomes

## When to Use

- Research tasks involving multiple Drive/Gmail sources
- Finding context around a document (who sent it, what was discussed)
- Creating or editing Google Docs/Sheets/Slides
- Inbox triage — searching, reviewing, and batch-acting on Gmail threads
- Composing email drafts (new or reply)
- Any task needing cross-source exploration

## Boundaries

- Task doesn't involve Google Workspace (no Drive or Gmail)
- Pure filesystem operations

## Success Criteria

This skill works when:
- Gmail searches use operators, not keyword soup
- Drive searches use keywords, not Gmail operators
- `comments.md` is checked after every doc/sheet/slides fetch
- `email_context` hints are followed to source emails
- Large results are filtered before reading
- Research tasks follow the exploration loop, not single-search-and-stop
- Triage uses batch operations, not one-thread-at-a-time
- `label` is used for mark_read/unread/unstar rather than seeking separate operations
- Errors are reported with actionable guidance, not just "it failed"
