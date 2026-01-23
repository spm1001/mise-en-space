# CLAUDE.md

**mise-en-space** — Google Workspace MCP with mise-en-place philosophy: everything prepped, in its place, ready for Claude to cook with.

## What This Is

A complete rewrite of the Google Workspace MCP with:
- **Filesystem-first design** — content to disk, caller controls ingestion
- **Clean layer separation** — extractors → adapters → tools
- **Minimal verb surface** — find, fetch, action, help
- **Token efficiency** — dense output, trimmed fluff

## Architecture

```
extractors/     Pure functions, no MCP awareness (testable without APIs)
adapters/       Thin Google API wrappers (easily mocked)
tools/          MCP tool definitions (thin wiring layer)
workspace/      Per-session folder management (~/.mcp-workspace/)
server.py       FastMCP entry point
```

**Layer rules:**
- Extractors NEVER import from adapters or tools
- Adapters NEVER import from tools
- Tools wire adapters → extractors → workspace
- server.py just registers tools

## Verb Vocabulary

Based on research in `mcp-google-workspace/docs/archive/mcpv2/Deep-dive code analysis...`:

```
find_*          Discovery, returns metadata only (NEVER writes files)
  find_drive_files(query) → [{id, name, mimeType, modified}]
  find_gmail_threads(query) → [{threadId, subject, snippet, date}]

fetch_*         Retrieval, deposits to working folder (ALWAYS writes, returns path)
  fetch_drive_file(fileId, format?) → {path: "~/.mcp-workspace/.../file.md"}
  fetch_gmail_thread(threadId) → {path: "~/.mcp-workspace/.../thread.txt"}

action_*        Mutations (minimal surface)
  action_send_gmail(to, subject, body)
  action_create_drive_file(name, content, parent?)

help            Self-documentation
```

**Key behaviors:**
- `find_*` tools NEVER write files — metadata only
- `fetch_*` tools ALWAYS write to account folder, return path
- Filenames use IDs for deduplication
- Pagination is opaque (cursors managed internally)

## Adapter Patterns

From research, adopt these patterns:

**1. Service decorator injection** (taylorwilsdon pattern):
```python
@require_google_service("drive", "drive_read")
async def list_files(service, query: str):
    # service auto-injected, cached, scoped
    return service.files().list(q=query, fields="files(id,name,mimeType)").execute()
```

**2. Always use `fields` parameter** — partial responses reduce payload:
```python
# Bad: returns all fields
service.files().list(q=query).execute()

# Good: only what's needed
service.files().list(q=query, fields="files(id,name,mimeType)").execute()
```

**3. True Gmail batch** via `/batch` endpoint (not app-level chunking):
```python
# Bad: 50 API calls
for msg_id in message_ids[:50]:
    service.users().messages().get(userId='me', id=msg_id).execute()

# Good: 1 API call for 100 operations
batch = service.new_batch_http_request()
for msg_id in message_ids[:100]:
    batch.add(service.users().messages().get(userId='me', id=msg_id))
batch.execute()
```

**4. Native markdown export** for Docs (not text/plain):
```python
drive.files().export(fileId=file_id, mimeType="text/markdown")
```

## Extractor Interface

Extractors are pure functions. They receive API response data, return processed content.

```python
# extractors/docs.py
def extract_doc_content(doc_response: dict) -> str:
    """
    Args:
        doc_response: Raw response from Docs API documents.get()

    Returns:
        Markdown string
    """
    # Pure transformation, no API calls
    return markdown_content
```

**What extractors receive:** Raw API response dicts
**What extractors return:** Processed content (markdown, CSV, structured text)
**What extractors NEVER do:** Make API calls, write files, access filesystem

## Development

```bash
uv sync                     # Install dependencies
uv run python server.py     # Run MCP server
uv run pytest               # Run tests
uv run pytest tests/unit    # Unit tests only (fast, mocked)
```

## Porting from v1

Battle-tested extractors to port from `mcp-google-workspace`:
- `tools/docs.py` → `extractors/docs.py`
- `tools/sheets.py` → `extractors/sheets.py`
- `tools/slides.py` → `extractors/slides.py`
- `tools/gmail.py` → `extractors/gmail.py` (signature stripping logic)

**Porting process:**
1. Copy the extraction logic (the pure transformation part)
2. Remove all `get_*_service()` calls — these become adapter responsibility
3. Change function signature to accept API response dict
4. Add type hints for input/output
5. Write unit test with fixture (mocked API response → expected output)

**Example transformation:**
```python
# v1 (entangled with API)
def read_doc_as_markdown(file_id: str) -> dict:
    service = get_docs_service()
    doc = service.documents().get(documentId=file_id).execute()
    # ... extraction logic ...
    return {"content": markdown}

# v2 (pure function)
def extract_doc_content(doc_response: dict) -> str:
    # ... same extraction logic, but receives doc_response as input ...
    return markdown
```

## OAuth

Credentials are symlinked from v1 (shared OAuth):
- `credentials.json` → `../mcp-google-workspace/credentials.json`
- `token.json` → `../mcp-google-workspace/token.json`

To re-authenticate: `cd ../mcp-google-workspace && uv run python -m workspace_mcp.auth`

## File Deposit Structure

```
~/.mcp-workspace/
├── config/
│   └── accounts.json           # Multi-account registry (future)
├── [account@domain.com]/
│   ├── drive/
│   │   ├── {fileId}.md         # Fetched files by ID
│   │   └── index.json          # Local metadata cache (future)
│   ├── gmail/
│   │   ├── {threadId}.txt      # Fetched threads
│   │   └── attachments/        # Downloaded attachments
│   └── calendar/
│       └── {eventId}.json      # Fetched events (future)
└── temp/                       # Auto-cleanup on server start
```

**workspace/manager.py responsibilities:**
- Create account folders on first use
- Generate file paths from IDs
- Clean temp/ on startup
- Return paths (never content) to tools layer

## Key Design Decisions

Decisions made during planning (Jan 2026) that future Claude should understand:

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **No `purpose` parameter** | Always LLM-analysis | This MCP is Claude's sous chef — always preparing for LLM consumption. Archival/editing modes are YAGNI. |
| **markitdown over PyMuPDF** | markitdown | PyMuPDF is 35x faster but AGPL licensed. markitdown is MIT and "good enough" for 80% of PDFs. Revisit if perf becomes an issue. |
| **MCP SDK v1.x not v2** | Pin to `>=1.23.0,<2.0.0` | v2 is pre-alpha (Q1 2026 expected stable). Core FastMCP patterns are identical; migration will be version bump not rewrite. |
| **4 verbs not 17 tools** | search, fetch, create, help | v1 had 17 tools. Claude doesn't need that many levers. Unified search + polymorphic fetch covers 95% of use cases. |
| **ID auto-detection** | fetch(id) figures out type | Gmail thread IDs look different from Drive file IDs. Server detects, no explicit source param needed. |
| **Pre-exfil detection** | Check "Email Attachments" folder | User runs background extractor. Value isn't speed (Gmail is 3x faster); value is Drive fullText indexes PDF *content*. |

## Research References

Key research informing this design:

- `mcp-google-workspace/docs/archive/mcpv2/Deep-dive code analysis of Google Workspace MCP implementations.md` — **READ THIS** for adapter patterns, verb vocabulary, anti-patterns to avoid
- `mcp-google-workspace/docs/V2.md` — Authoritative spec, build plan, phase tracking
- `mcp-google-workspace/docs/EXPERIMENTS.md` — Timing benchmarks, API discoveries

## Related

- `mcp-google-workspace` — v1 (source for porting)
- Bead: `mcp-google-workspace-awq` — tracks v2 epic
