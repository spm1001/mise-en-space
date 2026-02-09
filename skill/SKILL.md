---
name: mise
description: Orchestrates content fetching via mcp__mise__ tools. MANDATORY before using search/fetch/create — load FIRST when you see 'fetch this URL', 'get this blog post', 'extract content from', 'search Drive', 'search Gmail', 'find docs about', 'fetch this document', 'research in Workspace'. Prevents keyword-soup searches, missed comments, and orphaned context using file→email→meaning loop pattern, Gmail operators, comment checking, and result filtering the tools alone don't know. (user)
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

**Deposit accumulation:** `mise-fetch/` grows without bound during a session. Be aware during heavy research — 15+ deposits add up.

## The Three Tools

| Tool | Purpose | Output |
|------|---------|--------|
| `search` | Find files/emails | Path to deposited JSON + counts |
| `fetch` | Extract content to disk | Deposit folder: content.md, comments.md, manifest.json |
| `create` | Make new Google Doc/Sheet/Slides | File ID + web URL |

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
jq '.drive_results[:5] | .[] | {name, id}' mise-fetch/search--*.json
jq '.drive_results[] | select(.name | test("framework"; "i"))' mise-fetch/search--*.json
```

Rule of thumb: <10 results → just read. >15 → filter with jq first.

See `references/filtering-results.md` for patterns.

## Workflow 4: Create

**When:** "Make a Google Doc from this" / "Create a slide deck"

```python
create("# Meeting Notes\n\n- Item 1", title="Team Sync")
create(content, title="Report", doc_type="doc", folder_id="1xyz...")
```

Without `folder_id`, the doc lands in Drive root. Pass a folder ID when the user has a specific destination.

## Web Content

`fetch` handles any `http://` or `https://` URL:

- **HTML** → clean markdown via trafilatura (removes boilerplate, preserves code blocks)
- **Raw files** (JSON, Python, TOML) → pass-through with code fences
- **JS-rendered pages** → browser fallback (requires `webctl start`)
- **PDFs at URLs** → text extraction

Cleaner than `curl` (raw HTML) or `WebFetch` (lossy summary). Deposits to `mise-fetch/web--{title}--{hash}/content.md`.

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
- Creating Google Docs/Sheets/Slides from markdown
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
