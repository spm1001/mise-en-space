---
name: mise
description: Orchestrates content fetching via mcp__mise__ tools. MANDATORY before using search/fetch/do — load FIRST when you see 'fetch this URL', 'get this blog post', 'extract content from', 'search Drive', 'search Gmail', 'find docs about', 'fetch this document', 'research in Workspace', 'move this file', 'create a doc'. Prevents keyword-soup searches, missed comments, and orphaned context using file→email→meaning loop pattern, Gmail operators, comment checking, and result filtering the tools alone don't know. (user)
---

# mise

Content fetching for web URLs, Google Drive, and Gmail — via the mise-en-space MCP.

**Iron Law: Files are artifacts. Emails are meaning.**

A document tells you *what* was decided. The email thread tells you *why*, who pushed back, and what concerns remain.

## Always: Pass base_path

**MCP servers run as separate processes.** Without `base_path`, deposits land in the server's directory — not yours.

```python
# ALWAYS include base_path
search("Q4 planning", base_path="/Users/modha/Repos/my-project")
fetch("1abc...", base_path="/Users/modha/Repos/my-project")
```

**Deposit accumulation:** `mise/` grows without bound during a session. Be aware during heavy research — 15+ deposits add up.

## The Three Tools

| Tool | Purpose | Output |
|------|---------|--------|
| `search` | Find files/emails | Path to deposited JSON + counts |
| `fetch` | Extract content to disk | Deposit folder: content.md, comments.md, manifest.json |
| `do` | Act on Workspace (create, move, overwrite, edit) | File ID + web URL + cues |

`fetch` auto-detects input: Drive file ID, Drive URL, Gmail thread ID, or web URL.

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

`manifest.json` is still on disk for scripts/jq, but `cues` surfaces the actionable signals so you don't need to read it separately.

See `references/deposit-structure.md` for folder layout and attachment patterns.

## Workflow 1: Quick Fetch

**When:** "Get me this doc" / "Fetch this URL" / "Read that email thread"

```python
fetch("1abc...", base_path="...")                          # Drive file
fetch("https://docs.google.com/...", base_path="...")      # Drive URL
fetch("https://example.com/article", base_path="...")      # Web URL
fetch("18f3a4b...", base_path="...")                       # Gmail thread
fetch("thread_id", attachment="budget.xlsx", base_path="...")  # Single attachment
```

**Gmail URL gotcha:** Browser URLs contain web-format IDs (`FMfcgz...`), not API IDs. The MCP converts automatically, but conversion fails for self-sent emails (~2018+). If fetch errors on a Gmail URL, ask the user for the thread ID.

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
search("Elizabeth Kiernan Lantern data privacy contracts")

# GOOD — operators target precisely
search("from:elizabeth@example.com after:2025/12/01", sources=["gmail"])
search("filename:strawman from:legal@thinkbox.tv", sources=["gmail"])
search("has:attachment subject:lantern after:2025/12/01", sources=["gmail"])
```

Key operators: `from:`, `to:`, `filename:`, `has:attachment`, `after:`, `before:`, `subject:`, `in:sent`

See `references/gmail-operators.md` for the full set.

### Drive: Keywords Only (Different Syntax!)

Drive search uses plain keywords — **not Gmail operators.** `from:`, `is:starred`, `subject:` will return 400 errors on Drive.

```python
search("Q4 budget", sources=["drive"], base_path="...")     # Drive only
search("budget 2026", base_path="...")                       # Both sources
```

### Triage Large Results

When search returns 20+ results, don't read the full JSON. Filter first:

```bash
jq '.drive_results[:5] | .[] | {name, id}' mise/search--*.json
jq '.drive_results[] | select(.name | test("framework"; "i"))' mise/search--*.json
```

Rule of thumb: <10 results → just read. >15 → filter with jq first.

See `references/filtering-results.md` for patterns.

## Workflow 4: Do (Act on Workspace)

**When:** "Make a Google Doc from this" / "Move this file" / "Update that doc" / "Add a note to the meeting minutes"

### The 6 Operations

| Operation | What it does | Key params |
|-----------|-------------|------------|
| `create` | New Doc/Sheet/Slides | `content`+`title` OR `source` |
| `move` | Move file between folders | `file_id`, `destination_folder_id` |
| `overwrite` | Replace full doc content | `file_id`, `content` OR `source` |
| `prepend` | Insert at start of doc | `file_id`, `content` |
| `append` | Insert at end of doc | `file_id`, `content` |
| `replace_text` | Find-and-replace in doc | `file_id`, `find`, `content` |

### Choosing the Right Edit Operation

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

### Create and Move

```python
# Create doc
do(operation="create", content="# Meeting Notes\n\n- Item 1", title="Team Sync")
do(operation="create", content=content, title="Report", doc_type="doc", folder_id="1xyz...")

# Create sheet (see Sheet Creation below for details)
do(operation="create", content="Name,Score\nAlice,95\nBob,87", title="Results", doc_type="sheet")

# Move
do(operation="move", file_id="1abc...", destination_folder_id="1xyz...")
```

**Create:** Without `folder_id`, the doc lands in Drive root. Response includes `cues.folder` showing where it landed.

**Move:** Enforces single parent — removes all existing parents, adds destination. Response includes `cues.destination_folder` (name) and `cues.previous_parents`.

### Overwrite

```python
# Full replacement from inline markdown
do(operation="overwrite", file_id="1abc...", content="# Q4 Report\n\nRevised findings...", base_path="...")

# From a deposit folder (fetch → edit locally → publish back)
do(operation="overwrite", file_id="1abc...", source="mise/doc--q4-report--1abc/", base_path="...")
```

Markdown headings (`#`, `##`, etc.) are converted to Google Docs heading styles. Response includes `cues.char_count` and `cues.heading_count`.

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
do(operation="create", doc_type="sheet", source="mise/sheet--budget--abc123/", base_path="...")
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

## Web Content

`fetch` handles any `http://` or `https://` URL:

- **HTML** → clean markdown via trafilatura (removes boilerplate, preserves code blocks)
- **Raw files** (JSON, Python, TOML) → pass-through with code fences
- **JS-rendered pages** → browser fallback (requires `webctl start`)
- **PDFs at URLs** → text extraction

Cleaner than `curl` (raw HTML) or `WebFetch` (lossy summary). Deposits to `mise/web--{title}--{hash}/content.md`.

**Choosing between mise and passe for web content:** For a single known URL, `mise fetch` is cleaner (no goto+wait dance, better markdown output). For discovering page structure or crawling a multi-page site, use passe `snapshot` to find the nav tree then `read` each page.

## Gmail Attachments

PDFs and images are extracted eagerly. **Office files (DOCX/XLSX/PPTX) are skipped** during thread fetch (5-10s each). Extract on demand:

```python
fetch("thread_id", attachment="budget.xlsx", base_path="...")
```

See `references/deposit-structure.md` for the full attachment layout.

## Error Handling

| Error | Meaning | What to do |
|-------|---------|------------|
| `AUTH_EXPIRED` | OAuth token stale | Tell user to run `uv run python -m auth` in mise-en-space |
| `NOT_FOUND` | File/thread doesn't exist | Verify the ID; file may have been deleted or moved |
| `PERMISSION_DENIED` | No access to resource | Tell user they need to request access |
| `RATE_LIMITED` | Hit API quota | Wait 30s and retry once |
| `EXTRACTION_FAILED` | Couldn't parse content | Report to user with the file type and error detail |

## Anti-Patterns

| Pattern | Problem | Fix |
|---------|---------|-----|
| Keyword soup in Gmail | Noisy, imprecise results | Use `from:`, `filename:`, `after:` operators |
| Gmail operators in Drive search | 400 error from API | Drive uses plain keywords, not `from:`/`is:` |
| Skip comments.md | Miss the real discussion | Check after every doc/sheet/slides fetch |
| Ignore email_context | Miss the story behind the file | Follow the exploration loop |
| Read full search JSON | Token waste on 35 results | Filter with jq first |
| Stop after first search | Shallow understanding | Loop: new terms → new searches |
| Omit base_path | Deposits vanish into server directory | Always pass it |
| Overwrite a doc with images/tables | Content destroyed, not recoverable | Use `prepend`/`append`/`replace_text` |
| `replace_text` without checking cues | Silent no-op if text not found | Check `cues.occurrences_changed > 0` |

## Integration

**Composes with:**
- **arc** — Research tasks often originate as arc actions; findings feed back into arc items
- **todoist-gtd** — @Claude inbox items may request research; results inform outcomes
- **filing** — mise fetches context; filing processes and files it
- **mem** — "Have we researched this before?" Check mem before re-searching

## When to Use

- Research tasks involving multiple Drive/Gmail sources
- Fetching web content (cleaner than curl/WebFetch)
- Finding context around a document (who sent it, what was discussed)
- Creating or editing Google Docs/Sheets/Slides
- Any task needing cross-source exploration

## When NOT to Use

- Task doesn't involve content fetching (no web, Drive, or Gmail)
- Pure filesystem operations

## Success Criteria

This skill works when:
- Gmail searches use operators, not keyword soup
- Drive searches use keywords, not Gmail operators
- `comments.md` is checked after every doc/sheet/slides fetch
- `email_context` hints are followed to source emails
- Large results are filtered before reading
- Research tasks follow the exploration loop, not single-search-and-stop
- Errors are reported with actionable guidance, not just "it failed"
