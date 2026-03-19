# Changelog

## [0.2.0] - 2026-03-18

Batterie-wide consistency pass: docs consolidation, CI, versioning.

### Added
- OAuth token stored in macOS Keychain (not token.json)
- Remote server mode with StreamableHTTP transport and safe-tool filter
- Two-phase `--remote`/`--code` auth flow (replaces `--manual`)

### Changed
- Dropped googleapiclient: deleted services.py, stripped old mocking infra, simplified retry
- PII scrub: replaced real names/emails with fictional data

### Fixed
- MCP server path: use CLAUDE_PLUGIN_ROOT instead of "."
- MCP conflict: renamed .mcp.json to mcp-local.json

## 2026-03-13–15 — httpx Migration & Remote Mode

### Added
- Remote server mode for Claude.ai custom connectors (StreamableHTTP on `/mcp`)
- Content returned inline for remote fetch (no filesystem deposits)

### Changed
- Complete httpx migration: all adapters (gmail, charts, conversion, sheets, slides, calendar, activity, drive, docs) moved from googleapiclient to httpx
- Removed all web fetching code (mise is now Workspace-only: Drive, Gmail)

### Fixed
- 401 retry in httpx clients

## 2026-03-05–08 — Input Validation & Calendar

### Added
- Unit tests for do() input validation hardening
- Plain file creation and editing support in do()
- Calendar and Tasks API exploration, data shape documentation

### Changed
- Hardened do() input validation: IDs, path traversal, control chars

## 2026-02-27 — Plugin System

### Added
- Plugin manifest for Claude Code plugin system
- `.mcp.json` for plugin MCP server discovery
- Skill directory moved from `skill/` to `skills/workspace/`

## 2026-02-24 — Gmail Write Operations

### Added
- Gmail archive, label, and star via do() verb
- Gmail draft compose and threaded reply drafts via do()
- Calendar search source with Drive enrichment
- Activity API as search source
- Calendar and Tasks adapters with full test coverage

### Changed
- Clean layer violations: extractors pure, adapters named correctly
- Consolidated fixture loading: all tests use shared `load_fixture()`

## 2026-02-20–22 — Folder Navigation & Image Safety

### Added
- Drive folders navigable as first-class resources
- Resize oversized images before deposit (instead of skipping)
- PIL validation on all external-source image deposits
- SIGTERM handling fix (`os._exit()` instead of `sys.exit()`)
- `launch.sh` to fix MCP server orphaning on session exit

### Fixed
- Gmail image deposit safety: validated exfil matching + API image guards
- Wipe stale files on deposit folder re-fetch

## 2026-02-17–18 — do() Operations & Sheet Creation

### Added
- Surgical doc edits: prepend, append, replace_text, overwrite
- Multi-tab sheet creation via Sheets API hybrid path
- Split multi-tab spreadsheet deposits into per-tab CSV files
- Always-on PDF page thumbnails with platform-adaptive rendering
- Integration tests for PDF thumbnail rendering

### Changed
- Refactored do() dispatch: shared DoResult, self-validating operations
- Normalised do() response shape: all operations return operation + cues

### Fixed
- UTF-16 index bug in heading styles

## 2026-02-15 — do() Verb & XLSX Support

### Added
- `do(operation=move)` and post-action cues on create
- Renamed create tool to `do(operation=...)` for action verb scaffolding
- XLSX fetch returns all tabs via Sheets API path
- Preserved forwarded messages in Gmail thread extraction
- Auto-fallback to browser on 403/401, actionable error messages

### Fixed
- Hostile site defences (redirect loops, HTML size bombs)
- Empty thread_id guard in Gmail batch callback

## 2026-02-09 — Test Coverage Push & Titans Review

### Added
- Test coverage from 0% to 100% across all adapters (gmail, drive, sheets, docs, slides, activity)
- Cues/preview in tool responses
- Parallel Drive + Gmail search (2.3s to 0.8s)
- Benchmarks and Gmail attachment integration tests

### Changed
- Rewrote mise skill: workflow-organized, evidence-based design
- Capped thumbnail concurrency at 2 workers

### Fixed
- Titans review: 4 critical-path items, 5 cherry picks, 39 cues tests

## 2026-02-07–08 — Web Content & Attachments

### Added
- Web content extraction (mise-web)
- Single-attachment fetch API for Gmail
- Pre-exfil Drive lookup for Gmail attachment extraction
- Gmail search fields mask for batch fetch (5x faster)

### Changed
- Consolidated PDF extraction, Office file routing, large web PDF streaming

## 2026-02-01 — Search & Deposit

### Added
- Search deposit pattern
- 60-second timeout on all Google API calls
- Signal handlers to fix MCP server hang on session exit
- Activity, Tasks, Calendar, and Labels API services
- Comment extraction from Drive files
- Gmail attachment content extraction

### Changed
- Consolidated to 3-verb model with automatic comment enrichment

## 2026-01-24–29 — Content Types & Architecture

### Added
- Chart rendering via Slides API
- Drive snippets, Gmail attachment names, PDF streaming
- MCP resources, large file streaming, mypy compliance
- PDF/Office integration and hybrid PDF extraction
- Video summary support via GenAI API
- Slides extractor with workspace deposit pattern
- Gmail extractor with real fixture infrastructure
- Docs extractor with full element taxonomy

## 2026-01-20–23 — Initial Release

### Added
- MVP scaffold: sheets extractor, auth, retry, typed infrastructure
- Search and fetch tools wired to clean architecture
- 15-bead epic plan with design decisions
- Renamed from previous project to mise-en-space
