# CLAUDE.md

**mise-en-space** — Content fetching MCP (web URLs, Google Drive, Gmail) with mise-en-place philosophy: everything prepped, in its place, ready for Claude to cook with.

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
workspace/      File deposit management (mise-fetch/ in cwd)
server.py       FastMCP entry point
docs/           Design documents and references
```

**Key reference:** `docs/information-flow.md` — Timing data, design decisions, and flow diagrams showing how data moves from APIs through extraction to deposit. Read this before making changes to the fetch/search paths.

**Layer rules:**
- Extractors NEVER import from adapters or tools (no I/O)
- Adapters NEVER import from tools
- Adapters MAY import parsing utilities from extractors
- Tools wire adapters → extractors → workspace
- server.py just registers tools

**Parsing utilities:** Extractors may contain pure parsing functions
(e.g., `parse_presentation`, `parse_message_payload`) that adapters import.
The key invariant is extractors never perform I/O.

### Adapter Specializations

| Adapter | Purpose |
|---------|---------|
| `adapters/drive.py` | File metadata, search, download, export, comments |
| `adapters/docs.py` | Google Docs API (multi-tab support) |
| `adapters/sheets.py` | Sheets API (batchGet for values) |
| `adapters/slides.py` | Slides API + thumbnail fetching |
| `adapters/gmail.py` | Gmail threads and messages |
| `adapters/activity.py` | Drive Activity API v2 (comment/edit activities) |
| `adapters/conversion.py` | **Shared** Drive upload→convert→export→delete pattern |
| `adapters/pdf.py` | PDF extraction (hybrid: markitdown → Drive fallback) |
| `adapters/office.py` | Office files (DOCX/XLSX/PPTX via Drive conversion) |
| `adapters/image.py` | Image files (raster + SVG→PNG rendering) |
| `adapters/genai.py` | Video summaries via internal GenAI API |
| `adapters/web.py` | Web content fetching (HTTP + browser fallback) |

**Layered pattern:** `pdf.py` and `office.py` use `conversion.py` for the shared Drive conversion logic. This keeps the specific adapters thin while centralizing temp file management.

**Image handling:** `image.py` downloads raster images directly and renders SVG to PNG via `rsvg-convert` (fallback: `sips` on macOS). SVGs deposit both the raw XML and rendered PNG so Claude can "see" the content.

## Optional: Video Summaries (chrome-debug)

For video/audio files, `fetch` can return AI-generated summaries from Google's internal GenAI API. This requires `chrome-debug` running on port 9222.

```bash
chrome-debug    # Start Chrome with debug port enabled
```

**How it works:**
1. `fetch` detects video/audio MIME types
2. Connects to CDP (Chrome DevTools Protocol) on port 9222
3. Gets browser cookies for authentication
4. Calls Google's internal GenAI API for pre-computed summaries

**Without chrome-debug:** Falls back gracefully — returns basic metadata with a hint to run `chrome-debug`.

**Why cookies?** Google's video summary API is internal (not OAuth-accessible). It requires browser session cookies, not API tokens. The `websockets` dependency is for CDP communication.

**Caveat:** The GenAI endpoint (`appsgenaiserver-pa.clients6.google.com`) is undocumented and may change without notice.

## Web Content Fetching

`fetch` also handles web URLs (any `http://` or `https://` URL that isn't a Google service). This makes mise-en-space a first-class web content extraction tool for agents.

**How it works:**
1. `fetch` detects web URLs by prefix (http:// or https://)
2. Tries fast HTTP fetch first
3. Detects if content needs browser rendering (JS-rendered pages)
4. Falls back to browser via `passe` if available and needed
5. Extracts clean markdown via `trafilatura`
6. Deposits to `mise-fetch/web--{title}--{url-hash}/`

**Example:**
```python
result = fetch("https://trafilatura.readthedocs.io/en/latest/")
# → mise-fetch/web--a-python-package-command-line-tool--3364f7aa45b8/content.md
```

**Content extraction uses trafilatura**, which:
- Removes boilerplate (headers, footers, navigation)
- Preserves main article content
- Converts to clean markdown with links, images, tables
- Recovers code block language hints where possible

**Auth detection:** Automatically detects common auth patterns:
- HTTP 401/403 responses
- Login page redirects
- Soft paywalls ("subscribe to continue")
- CAPTCHA challenges (Cloudflare, reCAPTCHA)

**Browser fallback:** For JS-rendered content (React SPAs, Next.js sites):
- Requires `passe` installed and Chrome Debug running on port 9222
- Uses passe's Readability.js + Turndown.js for content extraction (returns markdown directly, skipping trafilatura)
- Inherits browser session cookies for authenticated content
- SPA detection: three-tier heuristic (short HTML, empty body text, framework patterns)

**Limitations:**
- No automatic cookie injection yet (Phase 2)
- Browser fallback requires passe installed and Chrome Debug running
- Some heavily protected sites may still block extraction

## MCP Tool Surface (3 verbs)

The MCP exposes 3 tools to Claude:

| Tool | Purpose | Writes files? |
|------|---------|---------------|
| `search` | Find files/emails/contacts, return metadata | No |
| `fetch` | Download content to workspace, return path | Yes |
| `create` | Make new Doc/Sheet/Slides from markdown | No (creates in Drive) |

Documentation is provided via MCP Resources (static content), not a tool.

**Key behaviors:**
- `search` returns metadata only — Claude triages before fetching
- `fetch` writes to `mise-fetch/` in cwd, returns path
- `fetch` auto-detects ID type (Drive file ID vs Gmail thread ID vs URL)
- `fetch` accepts optional `attachment` param for extracting a specific Gmail attachment (including Office files skipped during eager extraction)
- **Comments are included automatically** — when fetching docs/sheets/slides, open comments are deposited as `comments.md` (sous-chef philosophy)
- Filenames use IDs for deduplication
- Pagination is opaque (cursors managed internally)

## Adapter Functions

Adapter functions mirror the MCP verbs exactly:

```python
# In adapters/drive.py
search_drive_files(query) -> list[dict]
fetch_drive_file(file_id) -> dict
create_drive_file(title, content) -> dict

# In adapters/gmail.py
search_gmail_threads(query) -> list[dict]
fetch_gmail_thread(thread_id) -> dict
```

Same vocabulary at every layer. No mental translation needed.

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

Extractors are pure functions. They receive typed dataclasses, return content strings.

```python
# extractors/sheets.py
from models import SpreadsheetData

def extract_sheets_content(data: SpreadsheetData, max_length: int | None = None) -> str:
    """Pure transformation, no API calls."""
    ...
```

**What extractors receive:** Typed dataclasses from `models.py` (SpreadsheetData, DocData, etc.)
**What extractors return:** Content strings (markdown, CSV, structured text)
**What extractors NEVER do:** Make API calls, write files, access filesystem

## Error Handling

Errors are structured via `MiseError` dataclass in `models.py`.

### Error Kinds

| Kind | Meaning | Retryable? |
|------|---------|------------|
| `AUTH_EXPIRED` | Token needs refresh | No (user action required) |
| `NOT_FOUND` | Resource doesn't exist | No |
| `PERMISSION_DENIED` | No access to resource | No |
| `RATE_LIMITED` | Hit API quota | Yes (with backoff) |
| `NETWORK_ERROR` | Connection failed | Yes |
| `INVALID_INPUT` | Bad parameters | No |
| `EXTRACTION_FAILED` | Couldn't process content | No |

### Layer Responsibilities

**Adapters:**
- Catch Google API exceptions
- Convert to `MiseError` with appropriate `ErrorKind`
- Include useful details (file_id, http_status, etc.)

```python
from models import MiseError, ErrorKind

try:
    result = service.files().get(fileId=file_id).execute()
except HttpError as e:
    if e.resp.status == 404:
        raise MiseError(ErrorKind.NOT_FOUND, f"File not found: {file_id}")
    elif e.resp.status == 403:
        raise MiseError(ErrorKind.PERMISSION_DENIED, f"No access to: {file_id}")
    elif e.resp.status == 429:
        raise MiseError(ErrorKind.RATE_LIMITED, "API quota exceeded", retryable=True)
    raise
```

**Extractors:**
- Handle malformed input gracefully where possible
- Raise `MiseError(ErrorKind.EXTRACTION_FAILED, ...)` for unrecoverable issues
- Never swallow errors silently

**Tools (MCP layer):**
- Catch `MiseError`, format for MCP response
- Include `retryable` hint so Claude knows whether to retry

```python
@mcp.tool()
def fetch(file_id: str) -> dict:
    try:
        # ... adapter + extractor calls ...
        return result.to_dict()
    except MiseError as e:
        return e.to_dict()  # {"error": True, "kind": "not_found", ...}
```

### MCP Response Shape

Success:
```json
{"path": "~/.mcp-workspace/.../file.md", "format": "markdown", ...}
```

Error:
```json
{"error": true, "kind": "not_found", "message": "File not found: abc123", "retryable": false}
```

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

## Warnings Pattern

Extractors are pure functions — no logging, no side effects. But we need visibility when things go wrong.

**Solution:** Data models have `warnings: list[str]` fields. Extractors populate them during processing.

```python
# Extractor populates warnings on the data object
def extract_sheets_content(data: SpreadsheetData, max_length=None) -> str:
    data.warnings = []  # Clear existing
    if some_sheet_is_empty:
        data.warnings.append("Sheet 'Summary' is empty")
    # ... extraction ...
    return content

# Caller reads warnings after extraction
content = extract_sheets_content(data)
manifest_extra = {"warnings": data.warnings}  # Goes to manifest.json
```

**Why mutate `data.warnings` instead of returning a tuple?**
- Preserves the simple `str` return type
- Caller already has the data object, can read warnings from it
- Consistent with slides extractor (which had this pattern first)

**What gets warned:**

| Extractor | Warnings |
|-----------|----------|
| Sheets | Empty sheets, truncation |
| Docs | Unknown element types, missing inline objects, truncation |
| Gmail | HTML conversion fallback, empty body, truncation |
| Slides | Missing objectId, other per-slide issues |
| Comments | Missing author name, truncation |

**Note:** `extract_message_content()` in gmail.py returns `tuple[str, list[str]]` because it processes individual messages, not thread-level data. The thread extractor aggregates these into `data.warnings`.

## OAuth

OAuth client credentials live in GCP Secret Manager (not in repo). Auth flow:

```bash
# First time (or to refresh tokens)
uv run python -m auth          # Opens browser, creates token.json locally

# Manual mode (for SSH/remote)
uv run python -m auth --manual
```

**Prerequisites:**
- `gcloud` CLI installed and authenticated
- Access to the GCP project (set in `oauth_config.py`)

**What happens:**
1. Fetches `mise-credentials` from Secret Manager (in-memory, not saved)
2. Runs OAuth flow, opens browser for consent
3. Creates `token.json` locally (your personal tokens, gitignored)

**To change the GCP project:** Edit `oauth_config.py` or use `--project` flag.

**Token refresh:** `token.json` contains both access token and refresh token. The Google auth library automatically refreshes expired access tokens using the refresh token on each API call — no manual intervention needed. The retry module's 401 handler (`clear_service_cache`) is a fallback for when the *refresh token itself* is revoked (rare — requires `uv run python -m auth` to re-consent).

## File Deposit Structure

Fetched content goes to `mise-fetch/` in the current working directory:

```
mise-fetch/
├── slides--ami-deck-2026--1OepZjuwi2em/
│   ├── manifest.json           # Self-describing metadata
│   ├── content.md              # Extracted text/markdown
│   ├── slide_01.png            # Thumbnails (1-indexed, zero-padded)
│   ├── slide_02.png
│   └── ...
├── doc--meeting-notes--abc123def/
│   ├── manifest.json
│   └── content.md
└── gmail--re-project-update--thread456/
    ├── manifest.json
    └── content.md
```

**Folder naming:** `{type}--{title-slug}--{id-prefix}/`
- Type: slides, doc, sheet, gmail
- Title: slugified, max 50 chars
- ID: first 12 chars for readability

**manifest.json:** Self-describing folder metadata:
```json
{
  "type": "slides",
  "title": "AMI Deck 2026",
  "id": "1OepZjuwi2emuHPAP-LWxWZnw9g0SbkjhkBJh9ta1rqU",
  "fetched_at": "2026-01-23T17:00:00+00:00",
  "slide_count": 43,
  "has_thumbnails": true
}
```

**workspace/manager.py responsibilities:**
- Create deposit folders with slugified names
- Write content, thumbnails, manifests
- List/parse existing deposits
- Cleanup old deposits (configurable age)

## Quality Gates

```bash
uv run pytest tests/           # 160 unit tests (skip integration by default)
uv run mypy models.py extractors/ adapters/ validation.py workspace/
```

Integration tests require `-m integration` flag and real credentials.

## Timing & Benchmarks

```bash
uv run python scripts/slides_timing.py [presentation_id]   # Compare API patterns
```

**Key finding (Jan 2026):** HTTP batch requests are NOT supported for Workspace editor APIs (Slides, Sheets, Docs) — Google disabled this platform feature in 2022. However, concurrent individual `getThumbnail` requests work with isolated service objects (one per thread), capped at 2 workers. Google rate-limits at 3+ concurrent calls (tested with 43 slides). Shared `httplib2` connections cause SSL corruption — each thread needs `build_slides_service()`. Benchmarked (Feb 2026): 3.2x faster for 43 slides (22s vs 71s).

**Office conversion profiling (Feb 2026):** Upload+convert dominates at 67-77% of total time (DOCX: 5.7-7.0s, XLSX: 4.4-4.9s). This is server-side conversion inside Google's `files().create()` — nothing we can optimise. Download (7-11%), export (9-21%), and delete (4-7%) are minor. The `source_file_id` copy path (already implemented) skips download+upload entirely — fastest when file is already in Drive.

**Selective thumbnails (Jan 2026):** Thumbnails are now **enabled by default** because selective logic makes them cheap. The extractor analyzes each slide and sets `needs_thumbnail=True` only for:
- Charts (visual IS the content)
- Images (unless single large image >50% = stock photo)
- Fragmented text (≥5 short pieces, layout matters)

Text-only slides and stock photos are skipped. Test presentation: 4 of 7 slides get thumbnails.

## Validation & ID Conversion

`validation.py` provides shared utilities for URL/ID handling:

| Function | Purpose |
|----------|---------|
| `extract_drive_file_id(url_or_id)` | Drive URL → file ID |
| `extract_gmail_id(url_or_id)` | Gmail URL/web ID → API ID (auto-converts) |
| `convert_gmail_web_id(web_id)` | Arsenal Recon algorithm for web→API conversion |
| `is_gmail_web_id()` / `is_gmail_api_id()` | Format detection |

**Gmail ID gotcha:** Web UI uses different IDs than API. URLs like `mail.google.com/.../FMfcgz...` have web IDs that need conversion. The conversion works for `thread-f:` format (normal emails) but fails for `thread-a:` format (self-sent emails ~2018+).

## Fixture Organization

```
fixtures/
├── docs/
│   ├── basic.json              # Synthetic: multi-tab with all element types
│   ├── real_multi_tab.json     # Real API: 3-tab test document
│   └── real_single_tab.json    # Real API: single tab document
├── sheets/
│   ├── basic.json              # Synthetic: escaping edge cases
│   └── real_spreadsheet.json   # Real API: test spreadsheet
├── gmail/
│   ├── thread.json             # Synthetic: 3-message thread
│   └── real_thread.json        # Real API: 2-message thread (sanitized)
└── slides/
    └── real_presentation.json  # Real API: 7-slide test presentation
```

**Synthetic vs Real:** Synthetic fixtures are hand-crafted for edge cases. Real fixtures are captured from Google APIs via `scripts/capture_fixtures.py` and sanitized via `scripts/sanitize_fixtures.py`.

**Capturing new fixtures:**
```bash
uv run python scripts/capture_fixtures.py --sanitize   # Fetch + sanitize (recommended)
uv run python scripts/capture_fixtures.py              # Fetch only
uv run python scripts/sanitize_fixtures.py             # Sanitize existing fixtures
```

**Test doc folder:** [Google Docs Test Suite](https://drive.google.com/drive/folders/1_UMRzD4KScPksrnrGPrpk4ioQmvUDhmX) contains test documents for fixture capture.

Fixtures are JSON. `tests/conftest.py` converts them to typed dataclasses.

## Key Design Decisions

Decisions made during planning (Jan 2026) that future Claude should understand:

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **No `purpose` parameter** | Always LLM-analysis | This MCP is Claude's sous chef — always preparing for LLM consumption. Archival/editing modes are YAGNI. |
| **PDF: hybrid extraction** | markitdown → Drive fallback | Try markitdown first (fast, MIT). If <500 chars extracted, fall back to Drive conversion (slower but handles image-heavy/complex PDFs). Benchmarked: Drive extracts 100-1000x more content from complex PDFs. PyMuPDF tested but offers no quality advantage over markitdown and has AGPL license. |
| **MCP SDK v1.x not v2** | Pin to `>=1.23.0,<2.0.0` | v2 is pre-alpha (Q1 2026 expected stable). Core FastMCP patterns are identical; migration will be version bump not rewrite. |
| **3 verbs not 17 tools** | search, fetch, do | v1 had 17 tools. Claude doesn't need that many levers. Unified search + polymorphic fetch covers 95% of use cases. The 3rd verb evolves from `create` → `do(operation=...)` to accommodate move/rename/share (Feb 2026 decision). Current `create` tool stays as-is until a real use case pulls us to implement `do`. |
| **ID auto-detection** | fetch(id) figures out type | Gmail thread IDs look different from Drive file IDs. Server detects, no explicit source param needed. |
| **Pre-exfil detection** | Check "Email Attachments" folder | User runs background extractor. Value isn't speed (Gmail is 3x faster); value is Drive fullText indexes PDF *content*. |
| **Sync adapters, async tools** | Adapters sync, tools can wrap | Google API client is synchronous. Adapters stay sync. For MCP v2 tasks (async dispatch), tools layer wraps with `asyncio.to_thread()`. Avoids rewriting adapters. |
| **Thread-safe services** | `@lru_cache` | Service getters use lru_cache for thread-safe caching. No manual dict + lock needed. |
| **Batch API calls** | Service-specific optimization | Not "always batch" — use most efficient pattern per service. See table below. |
| **Sheets: 2 calls not 1** | `get()` + `batchGet()` | `includeGridData=True` returns 44MB of formatting metadata vs 79KB for values-only. Benchmarked: 2 calls is 3.5x faster despite extra round-trip. |
| **Large file streaming** | 50MB threshold | Files >50MB stream to temp file instead of loading into memory. Prevents OOM on gigabyte PPTXs. Configurable via `MISE_STREAMING_THRESHOLD_MB` env var. The exact threshold matters less than having the safety net — any reasonable value catches gigabyte files. |
| **No search snippets** | `snippet: None` | Drive API v3 has no `contentSnippet` field. The API returns 400 if requested. `fullText` search finds files but doesn't explain *why* they matched. Discovered Jan 2026 via live testing. |
| **Gmail: no streaming** | Full response only | Gmail API doesn't support chunked downloads. Current implementation is correct. |
| **Search deposits to file** | Path + counts, not inline JSON | Filesystem-first consistency with fetch. Claude reads deposited JSON when needed. Saves ~5% tokens per search but scales better (10 parallel searches = 30-40k tokens avoided). Also enables jq/grep filtering before full read. |
| **cwd is MCP's directory** | Known limitation | MCP servers run as separate processes — `Path.cwd()` is their cwd, not Claude's. All deposits go to `mise-en-space/mise-fetch/`. Future fix: add `base_path` parameter to search/fetch. |
| **Web: trafilatura not Defuddle** | trafilatura (Python) | Best F1 score (0.883) in benchmarks, Python-native (no Node subprocess), battle-tested at scale. Defuddle (JS) preserves code hints better but requires Node. We work around trafilatura's code block mangling via pre-process/restore pattern instead of forking. |
| **Web: code block preservation** | Pre-process/restore | Extract `<pre>` blocks before trafilatura, replace with placeholders, restore after. Avoids forking trafilatura while preserving language hints. |
| **Web: raw text handling** | Detect and pass through | GitHub raw URLs, JSON APIs return non-HTML. Detect via Content-Type + URL extension, format appropriately (code fences for code, pass-through for markdown). |
| **Web: passe browser fallback** | passe (CDP) replaces webctl (Playwright) | passe's `read` verb injects Readability.js + Turndown.js, returns markdown directly. `WebData.pre_extracted_content` carries the result; tool layer skips trafilatura when set. Three-tier SPA detection: short HTML, empty body text, framework patterns. `passe run` closes its tab on exit, so `passe eval` after run reads the wrong tab — use original URL as `final_url` until passe adds it to the run summary JSON. |
| **Web: binary Content-Type routing** | Adapter captures raw bytes, tool routes by type | Web URLs that return `application/pdf` (or other binary types) are detected via Content-Type in the adapter, which captures `raw_bytes` on `WebData` and skips HTML inspection. Tool layer checks Content-Type and routes to the appropriate extractor (e.g., `extract_pdf_content`). Status code checks (404, 429, 500) run *before* binary detection. Only types with working extractors are in `BINARY_CONTENT_TYPES` — don't add types we can't process. |
| **Gmail: no inline attachment text** | Pointers, not inline content | Extracted attachment text goes to separate `{filename}.md` files. content.md gets a compact `**Extracted attachments:**` summary with `→ \`filename.md\`` pointers. Originally (voSovu) we inlined full PDF text into content.md "like Drive PDFs" — but a Gmail thread is a conversation with attachments, not a standalone document. Inlining bloated content.md 10x (28→300 lines) and created a need for truncation guards. Reversed Feb 2026 (a0a7a45). |
| **Gmail: single-attachment fetch** | `fetch(thread_id, attachment="file.xlsx")` | Office files are skipped during eager thread extraction (5-10s each). The `attachment` parameter on `fetch()` enables on-demand extraction of any specific attachment — including Office files. Routes through same extractors as Drive files. Pre-exfil Drive copies checked first. Deposit goes to self-contained folder (`mise-fetch/{type}--{title}--{id}/`). |
| **Activity API: two target types** | `_parse_target` handles both `driveItem` and `fileComment.parent` | Comment activities use `fileComment` target (with drive item nested in `parent`), non-comment activities use `driveItem`. Both have the same shape (name, title, mimeType). Discovered Feb 2026 via real fixture capture — unit tests with hand-crafted mocks missed this because they all used `driveItem`. |
| **Activity API: people IDs not names** | `personName` → `people/ID` treated as Unknown | Activity API returns opaque `people/ID` strings in `knownUser.personName`, not display names. `_parse_actor` detects and falls through to "Unknown". No workaround — the API doesn't expose names for privacy. |
| **Skill: MANDATORY for all operations** | Gate on every fetch/search/create, not just research | Evidence (Feb 2026): test Claudes skip skill and go straight to MCP tools — keyword soup in Gmail, miss comments, forget base_path. MANDATORY gate (CSO 80+) needed for skill to load at all. Keeping it mandatory for simple fetches too because: (1) can't predict which fetches become research, (2) post-fetch checklist (comments, email_context) applies universally, (3) overhead is one skill load, cost of not having it is real mistakes. Skill organized by workflow (quick fetch, research, precision search, create) not by verb. |
| **Fetch: cues in response** | Surface decision-tree signals inline, not buried in manifest | Blind test (Feb 2026): test Claude loaded skill, scored 5/6 — skipped manifest.json due to momentum after reading content.md. Root cause: checklist discipline fails under "get to the answer" pressure. Fix: `cues` block in every fetch response with `files`, `open_comment_count`, `warnings`, `content_length`, `email_context` (always present, null when absent). Gmail adds `participants`, `has_attachments`, `date_range`. ~25-70 tokens per fetch. Manifest stays on disk for scripts. |
| **Search: preview in response** | Top 5 results per source inline, full set on disk | Original blind test: test Claude guessed wrong field name (`threadId` vs `thread_id`). Fix: `preview` block inline. Second blind test (Feb 2026 wijupo): preview noticed, prevented field errors, but 3 items wasn't enough for triage — test Claude still read full files. Enriched to 5 items with richer metadata: Drive adds `email_context` (exfil provenance), Gmail adds `message_count` and `attachment_names` (triage signal). ~200-300 tokens. |

### Per-Service API Patterns

| Service | Optimal Pattern | Calls | Why |
|---------|-----------------|-------|-----|
| **Docs** | `get(includeTabsContent=True)` | 1 | Minimal overhead, all tabs in one response |
| **Sheets** | `get()` + `values().batchGet()` | 2 | `includeGridData` bloats payload 560x with formatting metadata |
| **Slides** | `get()` + concurrent `getThumbnail()` | 1+N | Batch not supported, but concurrent with isolated services works (3.2x faster at 2 workers). Google rate-limits at 3+ concurrent calls. Each thread needs its own service object — shared httplib2 causes SSL corruption. |
| **Gmail** | `threads().get()` + batch `messages().get()` | 2 | Thread metadata + full message bodies |

### Linked Content in Docs

When content is linked from other Google apps into a Doc:

| Source | What API exposes | What we output |
|--------|------------------|----------------|
| **Sheets chart** | `linkedContentReference.sheetsChartReference` with spreadsheet/chart ID | `[Chart: title (from spreadsheet X)]` |
| **Sheets table** | Native table structure (not a linked object) | Markdown table |
| **Slides** | Image only, `linkedContentReference: {}` (empty) | `![image](url)` |

**Slides link limitation:** The Docs API doesn't expose the source presentation ID for linked slides. Google stores it server-side (the UI's "Open source" works) but it's not in the API response. This is a known limitation we can't work around.

**inlineObjects is per-tab:** In multi-tab docs, `inlineObjects` lives at `documentTab.inlineObjects`, not at document level. The model reflects this: `DocTab.inline_objects`.

### Docs API Element Taxonomy

ParagraphElement types (from discovery doc):
- `textRun` — main text content
- `footnoteReference` — footnote markers
- `inlineObjectElement` — images, drawings, charts
- `horizontalRule`, `pageBreak`, `columnBreak` — structural breaks
- `equation` — math (currently just `[equation]` placeholder)
- `autoText` — page numbers, dates
- `person` — `@mentions`
- `richLink` — smart chips (Calendar, Sheets, etc.)
- `dateElement` — date chips

EmbeddedObject subtypes (in inlineObjects):
- `imageProperties` — actual images (includes linked slides rendered as images)
- `embeddedDrawingProperties` — Google Drawings
- `linkedContentReference` — linked charts from Sheets (only type currently implemented)

## Comments

Comments are fetched via `fetch_file_comments()` in `adapters/drive.py` and formatted via `extract_comments_content()` in `extractors/comments.py`.

**What's captured:**
- Author name and email address
- Comment content and creation date
- Resolved status
- Quoted text (anchor) — human-readable for Google Docs, empty for DOCX/Sheets
- Threaded replies with author/date

**File types that don't support comments:**

| Type | API Behavior |
|------|--------------|
| Folders | Returns 0 comments (no error) |
| Forms | 404 → `MiseError(INVALID_INPUT, "Comments not supported for form files")` |
| Shortcuts | 404 → same error (doesn't resolve to target) |
| Sites, Maps, Apps Script | Same 404 pattern |

The adapter pre-checks known unsupported MIME types and gives a clear error before hitting the API. Unknown types that return 404 also get a clear error message.

## Unsupported Content Types

| Content Type | Why Unsupported | Alternatives |
|--------------|-----------------|--------------|
| **Google Groups** | No read API exists. Groups Migration API is write-only (import). Google Takeout can export but has no programmatic API. Web scraping works but is impractically slow (~3s per topic). | Request Vault export from IT (requires license), or access group content via individual Gmail subscriptions (incomplete). |

**Research (Jan 2026):** Confirmed with Jay Lee (GAM creator) that Google has never provided a Groups content read API. This is a known gap Google hasn't addressed in 3-5 years.

## Field Reports

`field-reports/` captures real-world skill/tool gaps discovered during use. Pattern:
1. Notice gap while working (skill didn't trigger, output incomplete, workflow wrong)
2. Write field report: `field-reports/YYYY-MM-DD-context.md`
3. Fix the gap (update docs, skill, or code)
4. Commit together — field report explains *why* the change was needed

## Research References

Key research informing this design:

- `mcp-google-workspace/docs/archive/mcpv2/Deep-dive code analysis of Google Workspace MCP implementations.md` — **READ THIS** for adapter patterns, verb vocabulary, anti-patterns to avoid
- `mcp-google-workspace/docs/V2.md` — Authoritative spec, build plan, phase tracking
- `mcp-google-workspace/docs/EXPERIMENTS.md` — Timing benchmarks, API discoveries

## Related

- `mcp-google-workspace` — v1 (source for porting)
- Bead: `mcp-google-workspace-awq` — tracks v2 epic
