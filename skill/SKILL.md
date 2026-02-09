---
name: mise
description: Orchestrates content fetching via mcp__mise__ tools. MANDATORY before using search/fetch/create — load FIRST when you see 'fetch this URL', 'get this blog post', 'extract content from', 'search Drive', 'search Gmail', 'find docs about', 'fetch this document', 'research in Workspace'. Prevents keyword-soup searches, missed comments, and orphaned context using file→email→meaning loop pattern, Gmail operators, comment checking, and result filtering the tools alone don't know. (user)
---

# mise

Guide for content fetching — web URLs, Google Drive, Gmail — using the mise-en-space MCP.

**Iron Law: Files are artifacts. Emails are meaning.**

A document tells you *what* was decided. The email thread tells you *why*, who pushed back, and what concerns remain.

## The 3-Verb Model

mise-en-space exposes 3 tools:

| Tool | Purpose | Output |
|------|---------|--------|
| `search` | Find files/emails | Path to deposited JSON + counts |
| `fetch` | Extract content | Path to deposit folder with content.md, comments.md |
| `create` | Make new docs | File ID + URL |

**Filesystem-first:** Results go to `mise-fetch/` in the specified directory. Read what you need.

### Critical: Always Pass base_path

**MCP servers run as separate processes.** Without `base_path`, deposits land in the MCP server's directory — not yours.

```python
# ALWAYS pass base_path when calling via MCP
search("Q4 planning", base_path="/Users/modha/Repos/my-project")
fetch("1abc...", base_path="/Users/modha/Repos/my-project")
```

This puts `mise-fetch/` next to your project where you can read it. Without it, files disappear into the server's install directory.

**CLI users:** `mise search` / `mise fetch` from the command line use your shell's cwd automatically — no `base_path` needed.

## The Exploration Loop

Don't just search→fetch→read. Follow the loop:

```
1. Search Drive → find file
2. Check email_context → if present, the file came from an email
3. Search Gmail filename:X → find the email thread
4. Read email → discover new terms, people, context
5. Search again with new terms → repeat
```

This loop discovers **meaning** (in communications) behind **artifacts** (files).

See `references/exploration-loop.md` for details.

## After Every Fetch

mise automatically deposits alongside your content:
- `comments.md` — open comments (if any)
- `manifest.json` — metadata including `open_comment_count`

**Always check for `comments.md`.** The real discussion often lives in comments — disagreements, questions, suggestions that didn't make it into the final text.

```bash
# What's in my deposit?
ls mise-fetch/doc--strawman-framework--1abc/
# content.md  comments.md  manifest.json

# Don't skip the comments!
```

## Gmail Deposits: Attachment Structure

When fetching Gmail threads, attachments are **separate files** — not inlined into `content.md`.

**What you get:**
```
mise-fetch/gmail--re-project-update--abc123/
├── content.md              # Thread text + pointer summary
├── quarterly-report.pdf    # Original PDF binary
├── quarterly-report.pdf.md # Extracted PDF text (separate file)
├── chart.png               # Image attachment (deposited as-is)
└── manifest.json           # Metadata + attachment list
```

**In content.md**, extracted attachments appear as pointers at the bottom:
```
**Extracted attachments:**
- quarterly-report.pdf → `quarterly-report.pdf.md`
- chart.png (deposited as file)
```

**To read attachment content:** Read the `.pdf.md` file for extracted text, or view the image file directly. PDFs and images are eagerly extracted; Office files are not.

**Office files (DOCX/XLSX/PPTX) are skipped** during eager extraction (5-10s each). The manifest tells you what was skipped:
```python
# Extract a specific Office attachment on demand
fetch("thread_id", attachment="budget.xlsx", base_path="/path/to/project")
```

This deposits to its own folder (`mise-fetch/xlsx--budget--thread_id/`).

## Gmail Search: Use Operators

**Don't:** Throw keywords at search (keyword soup)
```python
# BAD
search("Elizabeth Kiernan Lantern data privacy contracts")
```

**Do:** Use Gmail operators for precision
```python
# GOOD
search("from:elizabeth@privacylawunlocked.com after:2025/12/01", sources=["gmail"])
search("filename:strawman from:legal@thinkbox.tv", sources=["gmail"])
```

See `references/gmail-operators.md` for full reference.

## Web Content Fetching

`fetch` also handles web URLs — any `http://` or `https://` that isn't a Google service:

```python
# Blog posts, documentation, articles
fetch("https://simonwillison.net/2024/Dec/19/one-shot-python-tools/")

# GitHub raw files (markdown, code, config)
fetch("https://raw.githubusercontent.com/fastapi/fastapi/master/pyproject.toml")

# API responses (JSON)
fetch("https://api.github.com/repos/anthropics/anthropic-cookbook")
```

**What it handles:**
- **HTML pages** — Extracts main content via trafilatura, removes boilerplate
- **Code blocks** — Preserves language hints (`python`, `typescript`, etc.)
- **Raw text** — Markdown, JSON, Python, TOML passed through directly with code fences
- **JS-rendered pages** — Falls back to browser (requires `webctl start`)

**Deposits to:** `mise-fetch/web--{title}--{hash}/content.md`

**Limitations:**
- CAPTCHA-protected sites (Wikipedia, some news) will fail
- Heavy JS apps may need browser fallback
- Auth-required pages need cookies (future feature)

## Filtering Large Results

When search returns `drive_count: 20, gmail_count: 15`:

**Don't:** Read the entire 35-result JSON file

**Do:** Filter with jq first
```bash
# Preview top 5
jq '.drive_results[:5] | .[] | {name, id}' mise-fetch/search--*.json

# Filter by name
jq '.drive_results[] | select(.name | test("framework"; "i"))' mise-fetch/search--*.json
```

See `references/filtering-results.md` for patterns.

## Quick Reference

### Search (parallel across sources)
```python
search("Q4 planning", base_path="/path/to/project")              # Both Drive + Gmail
search("budget 2026", sources=["drive"], base_path="/path/to/project")  # Drive only
search("from:boss@co.com", sources=["gmail"], base_path="/path/to/project")  # Gmail only
```

### Fetch (auto-detects type)
```python
fetch("1abc...", base_path="/path/to/project")                    # Drive file ID
fetch("https://docs.google.com/...", base_path="/path/to/project")  # Drive URL
fetch("18f3a4b5c6d7e8f9", base_path="/path/to/project")          # Gmail thread ID
fetch("https://example.com/blog/post", base_path="/path/to/project")  # Web URL
fetch("thread_id", attachment="report.xlsx", base_path="/path/to/project")  # Single attachment
```

### Create (markdown → Google Doc)
```python
create("# Meeting Notes\n\n- Item 1", title="Team Sync")
create(content, title="Report", folder_id="1xyz...")
```

## Anti-Patterns

| Pattern | Problem | Fix |
|---------|---------|-----|
| Keyword soup in Gmail | Poor precision | Use `from:`, `filename:`, `after:` operators |
| Read full search JSON | Token waste | Filter with jq first |
| Skip comments.md | Miss real discussion | Always check after fetch |
| Ignore email_context | Miss source thread | Fetch the linked email |
| Stop after first search | Shallow research | Follow the exploration loop |

## Large Fetch Deposits

For big email threads (32k+ tokens) or long docs, filter before full Read:

```bash
# Preview first 50 lines
head -50 mise-fetch/gmail--re-lantern--abc123/content.md

# Grep for specific topic
grep -A5 "controllership" mise-fetch/gmail--*/content.md

# Count messages in thread
grep -c "^## Message" mise-fetch/gmail--*/content.md
```

## When to Use

- Fetching web content (cleaner than curl/WebFetch — extracts article, removes boilerplate)
- Research tasks involving multiple Drive/Gmail sources
- Finding context around a document (who sent it, what was discussed)
- Assembling background for meetings or strategic documents
- Any task where you need to trace communication threads

## When NOT to Use

- Single-file operations (just use `fetch` directly)
- You already know the patterns
- Simple lookups that don't need exploration

## Success Criteria

This skill works when:
- You use Gmail operators instead of keyword soup
- You check `comments.md` after every doc fetch
- You follow `email_context` hints to find source emails
- You filter large results before reading
- You follow the exploration loop for research tasks
