---
name: mise
description: MANDATORY before using mcp__mise__ tools. Load FIRST when you see 'fetch this URL', 'get this blog post', 'extract content from', 'search Drive', 'search Gmail', 'find docs about', 'fetch this document', 'research in Workspace'. Uses file→email→meaning exploration loop plus Gmail operators, comment checking, web content extraction, and result filtering patterns the MCP tools alone don't know. (user)
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

**Filesystem-first:** Results go to `mise-fetch/` in cwd. Read what you need.

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
search("Q4 planning")                           # Both Drive + Gmail
search("budget 2026", sources=["drive"])        # Drive only
search("from:boss@co.com", sources=["gmail"])   # Gmail only
```

### Fetch (auto-detects type)
```python
fetch("1abc...")                                # Drive file ID
fetch("https://docs.google.com/...")            # Drive URL
fetch("18f3a4b5c6d7e8f9")                       # Gmail thread ID
fetch("https://example.com/blog/post")          # Web URL
fetch("https://raw.githubusercontent.com/.../README.md")  # Raw text
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
