# Changelog

## [Unreleased]

### Changed
- **server.py refactored to the registration shim it always claimed to be** (mise-jimohe, 1,318 → 344 lines). No behaviour change. Resource text moved to `resources/docs.py`; remote orchestration (`search_remote`/`fetch_remote`, `REMOTE_ALLOWED_OPS`) to `tools/remote.py`; dispatch machinery (`DISPATCH`, `REQUIRED_PARAMS`, `run_operation()`, do() descriptions) to `tools/dispatch.py`. `_REMOTE_MODE` stays in server.py at module load (decoration-time constraint).
- `tests/unit/test_architecture.py` jurisdiction extended: `LAYER_RULES` now covers `workspace/` and `resources/`; new discovery-based `FILE_RULES` police every root-level .py (entry-point and retry.py exceptions documented); server.py capped at 500 lines.
- README/CLAUDE.md drift sweep (mise-lijogi): verb table now says do (14 ops, was create), status Stable (was Beta), auth flags corrected (`--auto`/`--code`; `--manual` never existed), adapter table gained calendar/forms/charts/cdp, caches enumerated, broken skill-section code fence fixed.

### Fixed
- `ContentType` Literal missing `"form"` (workspace/manager.py) — toise finding.
- Path containment in `do(create)` used `str.startswith` (prefix-collision admits `/repo-evil` siblings) — now `Path.is_relative_to`.
- `setup_oauth` leaked the parent's log file handle after spawning the detached auth subprocess.
- **reply_all built a wrong cc list** (mise-lurumu) — two stacked bugs. `_parse_headers` matched header names case-sensitively, so Outlook's `CC:` (uppercase) was silently dropped and externally-sent messages lost their cc list (also affected participants extraction); header names now canonicalise case-insensitively per RFC 5322. And `do_reply_draft` never passed the authenticated email into `_infer_recipients_all`, so self-exclusion was dead code and the user's own address landed in cc. Verified live against the field-report thread: To: sender, Cc: original cc only.

## [0.7.4] - 2026-06-10

Two Gmail trust follow-ups from the 0.7.3 BARB-thread verification, plus
security dependency bumps. Field reports mise-nucupi, mise-dazode.

### Fixed
- **Duplicate participants under header-format variation** (mise-nucupi) — Gmail serialises the same recipient differently across messages (`"a@x.com" <a@x.com>` on some, `Alice <a@x.com>` on others), and exact-string dedup kept both, over-counting the thread cue's participants list. `_extract_participants` now parses via `email.utils.getaddresses`, dedups on the lowercased email part, and keeps the most informative display form (a display name that merely repeats the address counts as bare). Headers with no addr-spec (e.g. undisclosed-recipients) are kept verbatim. Verified live: the BARB thread returns 5 unique participants (was 6), all display-named.
- **Outlook octet-stream attachments invisible to eager preview** (mise-dazode) — the 0.7.3 resolver covered `fetch_attachment` (explicit fetch) but the eager-extraction loop in `fetch_gmail` still dispatched on the declared MIME, so an Outlook-sent PDF or PNG tagged `application/octet-stream` was silently skipped from inline preview. The loop now resolves once per attachment (filename-extension fallback) and dispatches on the resolved value; `att.mime_type` stays declared. Resolution surfaces as a cue warning. Octet-stream Office files now land in `skipped_office` with their fetch-individually hint instead of vanishing. Verified live: an Outlook-tagged requisition PDF eagerly extracts with inline preview.

### Security
- Bumped transitive deps clearing 4 Dependabot alerts: urllib3 2.6.3 → 2.7.0 (high ×2: cross-origin header forwarding in proxied redirects, decompression-bomb safeguard bypass), starlette 0.52.1 → 1.2.1 (Host-header validation), idna 3.11 → 3.18 (encode bypass). Remote-mode StreamableHTTP stack smoke-tested live across starlette's 1.0 major.

### Added
- 8 new unit tests: header-format-variant dedup, bare-email upgraded by display name, email-casing variation, unparseable-header fallback, eager octet-stream PDF/image extraction, unknown-extension still skipped, octet-stream XLSX routed to skipped_office.

## [0.7.3] - 2026-05-18

Three Gmail trust fixes shipping together — all surfaced by a single BARB
delivery thread where mise's extraction quietly returned wrong-by-omission
data three different ways. Catalogued via field reports mise-zojoma,
mise-vutato, mise-mugure.

### Fixed
- **Body truncation through quoted history** (mise-zojoma) — `_strip_trailing_contact_block` walked forward, matching benign short-line candidates (e.g. "Hope you are well.") and counting URL/phone density from the entire body below. Outlook-format reply preambles inline the previous message with its URL-rich signature, clearing the URL threshold from 60+ lines down and silently truncating the live reply. Now walks backwards from end-of-body, anchoring the cut to the actual signature start. Trade-off: Apple Mail style `FirstName\n\nFull Name\nTitle...` sigs keep the first-name line as a content boundary. Under-stripping is the safer trade for a research-workflow tool.
- **Aggressive-strip silence** (mise-zojoma) — `strip_signature_and_quotes` now returns `(body, warnings)`; reductions >80% emit a warning so silent body-eating is detectable via thread cues. Existing tests updated for the new contract.
- **CC/Bcc participants omitted** (mise-vutato) — thread cue's `participants` list read From-only, dropping CC and Bcc recipients. A multi-recipient thread with 3 hidden CCs returned 2 participants — a "Hi all" reply composed from that cue would silently drop the CC list. Added "Bcc" to `WANTED_HEADERS`, new `bcc_addresses` field on `EmailMessage`, extracted `_extract_participants()` helper walking From + To + Cc + Bcc across every message.
- **Outlook-tagged octet-stream attachments refused** (mise-mugure) — Outlook/Exchange clients label CSVs, JSON, XML and other text formats as `application/octet-stream`. `fetch_attachment` rejected them with "Cannot extract attachment with MIME type: application/octet-stream" despite the filename extension being clear. New `_resolve_attachment_mime()` falls back to filename extension for a known-safe set (.csv, .tsv, .txt, .json, .xml, .yaml, .md, .html, .docx, .xlsx, .pptx, .pdf, image types) and surfaces the resolution as a warning in cues. New text-file branch in `fetch_attachment` deposits CSV/JSON/XML/etc. as text without conversion; Office-tagged octet-stream files route through the existing office extractor. Unknown extensions are still refused honestly — no silent guessing.

### Added
- `fixtures/gmail/outlook_reply_url_dense_quote.txt` and `fixtures/gmail/standalone_msg_with_url_sig.txt` — redacted real-world fixtures (anonymised) reproducing the truncation bug shapes.
- 26 new unit tests across the three fixes covering: critical-paragraph survival, no-catastrophic-reduction, signature stripping, aggressive-strip warning, BARB participant scenario (5 expected), MIME resolver, octet-stream text/JSON/XLSX dispatch, unknown-extension still-refused.

## [0.7.1] - 2026-05-04

### Changed
- Renamed skill directory `skills/workspace/` → `skills/mise/` so Cowork's Customize UI displays the skill as "mise" alongside the `mise:mise` connector. Removes the risk of users mistaking it for an Anthropic-shipped Workspace skill.

## [0.7.0] - 2026-05-04

### Added
- `cues._identity.email` on every search/fetch/do response — self-discloses the authenticated Google account so callers can disambiguate when multiple Workspace connectors are loaded in the same session
- `cues_util.py` at root level — identity resolution lives here (not in models), with `current_user_email()`, `with_identity()`, `resolve_user_email_eager()` for crosscutting reuse
- `tests/unit/test_cues_util.py` (9 tests covering injection, eager-resolve happy/legacy/missing/idempotent/failure paths, autouse fixture meta-test)
- `tests/conftest.py` autouse fixture that defaults `current_user_email` to None — prevents the developer's live Keychain identity leaking into tests

### Changed
- `MiseSyncClient.__init__` now eagerly resolves identity (Drive `about` for legacy tokens, cached read for enriched ones). `to_dict()` is pure — no HTTP at serialisation time.
- `token_store.save_token` enriches new tokens with `_identity` at OAuth time; legacy tokens get backfilled lazily and the result written back to Keychain
- `tools/setup_oauth.py` already_authenticated path triggers sync client init so identity actually populates in the response cues
- `tools/share.py` preview path applies `with_identity` for consistency with the model-driven response builders
- `skills/mise/SKILL.md` rewritten for Cowork — removed CC-specific `/exit then claude` reactivation chatter and CLI-only OAuth walkthrough; leads with `mise.do(operation="setup_oauth")` as canonical bootstrap; added Identity & multi-account section pointing at `cues._identity`

### Fixed
- Broad `except Exception` swallowing in identity resolution — now logs at WARN with exception type and message instead of silently caching None forever

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
