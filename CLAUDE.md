# CLAUDE.md

**mise-en-space** — Content fetching MCP (web URLs, Google Drive, Gmail) with mise-en-place philosophy: everything prepped, in its place, ready for Claude to cook with.

## Architecture

```
extractors/     Pure functions, no MCP awareness (testable without APIs)
adapters/       Thin Google API wrappers (easily mocked)
tools/          MCP tool definitions (thin wiring layer)
workspace/      File deposit management (mise/ in cwd)
server.py       FastMCP entry point
docs/           Design documents and references
```

**Key references:** `docs/information-flow.md` (flow diagrams, timing data), `docs/decisions.md` (full design decision history with rationale).

**Layer rules:**
- Extractors NEVER import from adapters or tools (no I/O)
- Adapters NEVER import from tools
- Adapters MAY import parsing utilities from extractors
- Tools wire adapters → extractors → workspace
- server.py just registers tools

### Adapter Specializations

| Adapter | Purpose |
|---------|---------|
| `drive.py` | File metadata, search, download, export, comments |
| `docs.py` | Google Docs API (multi-tab support) |
| `sheets.py` | Sheets API (batchGet for values) |
| `slides.py` | Slides API + thumbnail fetching |
| `gmail.py` | Gmail threads and messages |
| `activity.py` | Drive Activity API v2 |
| `conversion.py` | **Shared** Drive upload→convert→export→delete pattern |
| `pdf.py` | PDF extraction (hybrid: markitdown → Drive fallback) |
| `office.py` | Office files (DOCX/XLSX/PPTX via Drive conversion) |
| `image.py` | Image files (raster + SVG→PNG rendering) |
| `genai.py` | Video summaries via internal GenAI API (requires chrome-debug) |
| `web.py` | Web content fetching (HTTP + passe browser fallback) |

## MCP Tool Surface (3 verbs)

| Tool | Purpose | Writes files? |
|------|---------|---------------|
| `search` | Find files/emails, return metadata + inline preview | No |
| `fetch` | Download content to `mise/` in cwd, return path + cues | Yes |
| `do` | Act on Workspace (create, move, overwrite, prepend, append, replace_text) | Varies |

**Key behaviors:**
- `search` returns metadata only — Claude triages before fetching
- `fetch` auto-detects ID type (Drive file ID vs Gmail thread ID vs URL)
- `fetch` accepts optional `attachment` param for extracting specific Gmail attachments
- `do` routes via `operation` param — `do(operation="create", ...)`
- **Comments included automatically** — open comments deposited as `comments.md`
- **Cues in every response** — `cues` block surfaces files, comment count, warnings, email context
- `base_path` is required on all tools — MCP servers run as separate processes, `Path.cwd()` is theirs not Claude's

## Error Handling

Errors are `MiseError` (in `models.py`) with `ErrorKind`: `AUTH_EXPIRED`, `NOT_FOUND`, `PERMISSION_DENIED`, `RATE_LIMITED`, `NETWORK_ERROR`, `INVALID_INPUT`, `EXTRACTION_FAILED`. Each includes `retryable` hint. Adapters catch Google exceptions and convert; tools catch `MiseError` and format for MCP response.

## Warnings Pattern

Data models have `warnings: list[str]` fields. Extractors populate them during processing (mutation, not return tuple — preserves simple `str` return type). Exception: `extract_message_content()` returns `tuple[str, list[str]]` for per-message processing.

## File Deposit Structure

```
mise/
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

## Gotchas

| Gotcha | Detail |
|--------|--------|
| **UTF-16 indices in Docs API** | All position-based operations must use `_utf16_len()` not Python `len()`. Helper in `tools/overwrite.py`. Emoji = 2 UTF-16 code units. |
| **Gmail web IDs ≠ API IDs** | `FMfcgz...` web IDs need conversion. Works for `thread-f:` but fails for `thread-a:` (self-sent ~2018+). See `validation.py`. |
| **No search snippets** | Drive API v3 has no `contentSnippet` field. `fullText` search finds files but doesn't explain why they matched. |
| **Pre-exfil detection** | User runs background extractor to Drive. Value is that Drive fullText indexes PDF *content*. Check "Email Attachments" folder. |
| **Overwrite destroys content** | `overwrite` is a full replacement — images, tables, formatting all lost. Use `prepend`/`append`/`replace_text` when existing content matters. |
| **No purpose parameter** | This MCP always prepares for LLM consumption. No archival/editing modes. |

## Development

```bash
uv sync                     # Install dependencies
uv run python server.py     # Run MCP server
uv run pytest               # Run tests
uv run pytest tests/unit    # Unit tests only (fast, mocked)
uv run mypy models.py extractors/ adapters/ validation.py workspace/
```

Integration tests require `-m integration` flag and real credentials.

## OAuth

```bash
uv run python -m auth          # Opens browser, creates token.json locally
uv run python -m auth --manual # SSH/remote mode
```

Credentials from GCP Secret Manager (in-memory). Token auto-refreshes; `clear_service_cache` handles revoked refresh tokens.

## How to Add a New Content Type

1. **Adapter** — Create `adapters/{type}.py` with fetch function (API calls, returns data)
2. **Extractor** — Create `extractors/{type}.py` with pure extraction function (data in, markdown out)
3. **Wire in tools** — Add handler in `tools/fetch/` and route in `tools/fetch/router.py`
4. **Model** — Add data model in `models.py` if needed
5. **Fixture** — Add to `fixtures/{type}/`, capture via `scripts/capture_fixtures.py`
6. **Tests** — Unit test for extractor (fixture → expected output), adapter mock test

## How to Add a New do() Operation

1. **Implementation** — Create `tools/{operation}.py` with `do_{operation}()` returning `dict` with `file_id`, `title`, `web_link`, `operation`, `cues`
2. **Route** — Add `elif operation == "{name}":` branch in `server.py` `do()` function
3. **Validate** — Check required params at the router, return `{"error": True, "kind": "invalid_input", ...}` for missing ones
4. **Resource docs** — Update `docs_do()` resource in `server.py` with new operation
5. **Tests** — Unit test for the implementation function

## Field Reports

`field-reports/` captures real-world skill/tool gaps. Pattern: notice gap → write field report → fix → commit together.
