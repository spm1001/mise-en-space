# CLAUDE.md

**mise-en-space** — Google Workspace MCP (Drive, Gmail) with mise-en-place philosophy: everything prepped, in its place, ready for Claude to cook with.

## Architecture

```
extractors/     Pure functions, no MCP awareness (testable without APIs)
adapters/       Thin Google API wrappers (easily mocked)
tools/          MCP tool definitions (thin wiring layer)
workspace/      File deposit management (mise/ in cwd)
server.py       FastMCP entry point (stdio default, --remote for StreamableHTTP)
docs/           Design documents and references
```

**Shared utilities (root level)** — infrastructure that multiple layers need but doesn't belong in any single layer:

| File | Purpose | Used by |
|------|---------|---------|
| `html_convert.py` | HTML→markdown via markitdown (needs tempfile — why it's not in extractors) | adapters |
| `filters.py` | Attachment filtering logic (`is_trivial_attachment`, `filter_attachments`) | adapters, tools |
| `validation.py` | ID/URL validation (`validate_drive_id`, `validate_gmail_id`, etc.) | tools, adapters |
| `retry.py` | Retry decorator with exponential backoff and jitter | adapters |
| `logging_config.py` | Structured logging setup (`logger`, `log_retry`) | everywhere |

**Key references:** `docs/information-flow.md` (flow diagrams, timing data), `docs/decisions.md` (full design decision history with rationale).

**Layer rules:**
- Extractors NEVER import from adapters or tools (no I/O, no tempfile, no os)
- Adapters NEVER import from tools
- Adapters MAY import parsing utilities from extractors
- Adapters use `convert_*` names, not `extract_*` (extract_* reserved for pure extractors/)
- Tools wire adapters → extractors → workspace
- server.py just registers tools
- Shared utilities live at root level — don't add new ones without understanding the pattern above

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
| `pdf.py` | PDF conversion (hybrid: markitdown → Drive fallback) |
| `office.py` | Office file conversion (DOCX/XLSX/PPTX via Drive) |
| `image.py` | Image files (raster + SVG→PNG rendering) |
| `genai.py` | Video summaries via internal GenAI API (requires chrome-debug) |

## MCP Tool Surface (3 verbs)

| Tool | Purpose | Writes files? |
|------|---------|---------------|
| `search` | Find files/emails/activity/calendar events, return metadata + inline preview | No |
| `fetch` | Download content to `mise/` in cwd, return path + cues | Yes |
| `do` | Act on Workspace (create, move, rename, share, overwrite, prepend, append, replace_text, draft, reply_draft, archive, star, label) | Varies |

**Key behaviors:**
- `search` returns metadata only — Claude triages before fetching
- `search` accepts `type=` for MIME filter: `folder`, `doc`, `spreadsheet`/`sheet`, `slides`, `pdf`, `image`, `video`, `form`. `query` is optional when `type` or `folder_id` is set.
- `fetch` auto-detects ID type (Drive file ID vs Gmail thread ID)
- `fetch` accepts optional `attachment` param for extracting specific Gmail attachments
- `fetch` accepts `recursive=True` on folder IDs — returns full indented tree (max depth 5, 1000 items)
- `do` routes via `operation` param — `do(operation="create", ...)`
- `do(move)` accepts `file_id` as a list for batch moves — validates destination once, returns per-file summary
- **Comments included automatically** — open comments deposited as `comments.md`
- **Cues in every response** — `cues` block surfaces files, comment count, warnings, email context
- `base_path` is required on all tools in stdio mode — MCP servers run as separate processes, `Path.cwd()` is theirs not Claude's. In remote mode, `base_path` is optional (temp dir used automatically).

## Remote Mode

`server.py --remote` (or `MISE_REMOTE=1`) runs as a StreamableHTTP server on `/mcp` for Claude.ai custom connectors. Key differences from stdio:

| Aspect | stdio (default) | remote (`--remote`) |
|--------|----------------|---------------------|
| Transport | stdin/stdout | StreamableHTTP on `/mcp` |
| `do()` operations | All 13 | 6 safe ops: create, draft, reply_draft, archive, star, label |
| Content delivery | Filesystem deposits | Inline in JSON-RPC response (`content` + `comments` fields) |
| `base_path` | Required | Optional (temp dir) |
| Tool description | Full | Restricted (only safe ops + relevant params) |
| Health endpoint | N/A | `/health` returns `{"status": "ok"}` |

**Architecture:** `_REMOTE_MODE` is determined at module load time (before `@mcp.tool()` decorators run) so tool descriptions adapt. This is intentional — argparse validates in `__main__` but the value must be available earlier for the conditional `description=` parameter on `@mcp.tool()`. Don't move this to argparse without understanding why it's early.

**Operation gating:** `_REMOTE_ALLOWED_OPS` in server.py. Rejected ops get a generic "not available in remote mode" error listing only allowed ops — restricted op names are not leaked.

**Binary content:** Image fetches in remote mode return metadata and cues but no inline content (binary can't be text-encoded). A cue warning explains this.

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
| **Overwrite uses Drive import** | Google Doc overwrite uses `files().update()` with `text/markdown` media type — same import engine as create. All markdown formatting (headings, bold, tables) renders automatically. No Docs API involved. |
| **Gmail web IDs ≠ API IDs** | `FMfcgz...` web IDs need conversion. Works for `thread-f:` but fails for `thread-a:` (self-sent ~2018+). See `validation.py`. |
| **No search snippets** | Drive API v3 has no `contentSnippet` field. `fullText` search finds files but doesn't explain why they matched. |
| **Pre-exfil detection** | User runs background extractor to Drive. Value is that Drive fullText indexes PDF *content*. Check "Email Attachments" folder. |
| **Overwrite destroys content** | `overwrite` is a full replacement — images, tables, formatting all lost. Use `prepend`/`append`/`replace_text` when existing content matters. |
| **No purpose parameter** | This MCP always prepares for LLM consumption. No archival/editing modes. |
| **Image size skip vs format skip asymmetry** | `att.size > 4.5MB` no longer causes a pre-download skip — oversized images are downloaded and resized. Unsupported MIME types (not in `SUPPORTED_IMAGE_MIME_TYPES`) still skip pre-download. Reason: size is fixable by resizing; unsupported format is not. Don't restore the size check without also removing the resize logic. |
| **get_deposit_folder wipes on re-fetch** | Every call to `get_deposit_folder` deletes existing files in that folder before returning it. This prevents stale files from previous fetches. Do NOT call `get_deposit_folder` twice for the same folder mid-operation (e.g. inside a retry loop) — the second call will wipe files the first call's writes produced. |
| **MCP server must restart after code changes** | The MCP server loads code at session start. Edits to `extractors/`, `adapters/`, `tools/`, `workspace/` are not live until the next Claude Code session. Smoke-test new features in a fresh session. |
| **Share requires confirm gate** | `do(operation="share")` without `confirm=True` returns a preview — the API won't execute. Call once to preview, show user, call again with `confirm=True`. Non-Google emails (iCloud, Outlook) automatically fall back to notification email (Google requires it); check `cues.notified` to see which recipients were notified. |
| **`_REMOTE_MODE` is early** | Set at module load, not in `__main__`. Required because `@mcp.tool(description=...)` fires at decoration time. Don't "clean up" by moving to argparse — breaks conditional tool descriptions. For containers, use `MISE_REMOTE=1` env var (not `--remote` flag) — `sys.argv` is fragile under process managers. |
| **Remote fetch retry risk** | `get_deposit_folder` wipes on re-call (see above). In remote mode, HTTP client retries or Kube probes can trigger double-wipe. Don't add automatic retry at the HTTP level for fetch operations. |
| **Remote is single-user** | One `token.json`, one `lru_cache(maxsize=1)` per service. Multi-tenancy would require per-request credential injection — architecturally significant. This is a confirmed design choice. |

## Development

```bash
uv sync                           # Install dependencies
uv run python server.py           # Run MCP server (stdio)
uv run python server.py --remote  # Run MCP server (StreamableHTTP on :8000/mcp)
uv run python server.py --help    # CLI help
uv run pytest                     # Run tests
uv run pytest tests/unit          # Unit tests only (fast, mocked)
uv run mypy models.py extractors/ adapters/ validation.py workspace/
```

Integration tests require `-m integration` flag and real credentials.

## OAuth

```bash
uv run python -m auth          # Opens browser automatically on macOS/Linux desktop
uv run python -m auth --manual # SSH/remote mode (paste redirect URL back)
uv run python -m auth --code URL_OR_CODE  # Non-interactive (for Claude/scripts)
```

`credentials.json` (OAuth client config, not secret) ships with the repo. Token auto-refreshes; `clear_service_cache` handles revoked refresh tokens. Maintainer can also fetch credentials from GCP Secret Manager as fallback.

## How to Add a New Content Type

1. **Adapter** — Create `adapters/{type}.py` with fetch function (API calls, returns data)
2. **Extractor** — Create `extractors/{type}.py` with pure extraction function (data in, markdown out)
3. **Wire in tools** — Add handler in `tools/fetch/` and route in `tools/fetch/router.py`
4. **Model** — Add data model in `models.py` if needed
5. **Fixture** — Add to `fixtures/{type}/`, capture via `scripts/capture_fixtures.py`
6. **Tests** — Unit test for extractor (fixture → expected output), adapter mock test

## How to Add a New do() Operation

1. **Implementation** — Create `tools/{op}.py` with `do_{op}()` that validates its own params (accepts `str | None`) and returns `DoResult` on success or error dict on failure
2. **Dispatch** — Add handler to `_DISPATCH` dict in `server.py`
3. **Register** — Add name to `OPERATIONS` in `tools/__init__.py`
4. **Export** — Add `do_{op}` to `tools/__init__.py` imports and `__all__`
5. **Resource docs** — Update `docs_do()` resource in `server.py` with new operation
6. **Tests** — Unit test for the implementation + `test_dispatch.py` verifies OPERATIONS/DISPATCH sync automatically

## Field Reports

`docs/` contains field reports capturing real-world skill/tool gaps. Pattern: notice gap → write field report → fix → commit together.
