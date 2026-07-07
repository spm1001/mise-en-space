# Changelog

## [1.3.1] - 2026-07-07

### Fixed
- `setup_oauth` no longer races itself: the tool mints the consent URL **once** (persisting the PKCE verifier) and the detached subprocess consumes it via the new `python -m auth --auto --url <url>` mode, instead of minting a second URL whose verifier overwrote the first — the returned URL is now always exchangeable (its `code_challenge` matches the persisted verifier). On headless boxes the subprocess now keeps a threaded callback listener alive for 5 minutes (an `ssh -L 3000:localhost:3000` tunnel delivers the callback), and the `--code` path is the documented fallback either way — the verifier survives listener timeout and failed exchanges (mise-zefahe)
- `setup_oauth` verifies credentials actually **load** before claiming `already_authenticated` — a present-but-revoked/corrupt token now falls through to a fresh flow with status `reauthenticating_stale_creds` and the load diagnostic in `cues.stale_creds_diagnostic`, instead of bouncing the user between "authed!" and auth errors (mise-didage)
- Port pre-check hardened and shared: `oauth_config.port_is_free` binds with SO_REUSEADDR to match the real listener's semantics (a TIME_WAIT socket from a just-finished flow no longer blocks retries for ~60s), and `python -m auth --auto` now pre-checks the port BEFORE opening a browser, so a busy port can't burn a consent click (mise-zanezo)

### Removed
- `find_events_for_file` (adapters/calendar.py): dead code — no production caller (drive enrichment uses `list_events` + the meeting-context index) — still carrying the single-page oldest-first cap bug that 1.3.0 fixed in `list_events`. Deleted rather than repaired (mise-kulefi)

### Docs
- CLAUDE.md Error Handling rewritten to the real, deliberate two-tier contract: API-facing adapters (drive, gmail) convert Google's HTTP taxonomy to `MiseError` kinds at the adapter layer; processing adapters (office, pdf, conversion, image, charts, forms, activity, calendar, genai, cdp) raise bare and every tools-layer funnel converts uniformly (fetch router / search per-source / `run_operation` / server.py backstop) — plus the rule for new adapters (mise-ceroru)

### Tests
- New `tests/unit/test_setup_oauth.py` (14 tests): creds-validity gate, single-mint invariant (spawn argv carries the returned URL; returned URL's challenge == S256(persisted verifier) through real jeton minting), callback-handler CSRF/state validation, CLI arg contract, `port_is_free` semantics. `run_operation`'s never-raises wrap pinned in test_dispatch.py (mise-jiberu)

## [1.3.0] - 2026-07-07

### Added
- Gmail search results carry latest-message triage signals: `last_sender` (From of the newest message — `from` remains the thread originator), `from_me` (tri-state: latest voice is the authenticated user; `null` when identity is unresolved — never read `null` as "theirs"), and `unread_count`. Zero extra API calls — the per-thread metadata fetch already held every message's headers and discarded them (mise-samono)
- Calendar search honours the query: free text rides the API's `q` param (matches summary/description/attendees/location). The ±7-day window is scanned in full (internal pagination, bounded at 500) and when more events match than `max_results`, the events **nearest to now** are kept — chronological order preserved — with a `cues.calendar_truncated` warning. Previously the query was silently ignored AND Google's oldest-first ordering + a single capped page meant tomorrow's meeting could never appear on a busy calendar: the cap ate the future (mise-bidopi)

### Changed
- Deposits land in `.mise/` (dot-named, hidden) instead of `mise/` — the piles stop polluting the visible tree; agents are unaffected because every response returns the deposit path explicitly. Existing `mise/` piles keep their old name and are not migrated; repos should gitignore `/.mise/` (mise-pamofa)
- Gmail search `snippet` now comes from the **latest** message in the thread (was the thread-list snippet, which Gmail draws from an arbitrary — often quoted/early — message; observed misattributing authorship during live triage)

### Docs
- SKILL.md Workflow-6 truncation claims corrected: results cap at `max_results` (default 20, no auto-pagination past it) and the cue key is `gmail_truncated`, not `truncated`. Calendar source section rewritten to the new semantics with an "is tomorrow's meeting still on?" example

## [1.2.2] - 2026-06-28

### Added
- `comment_reply` do() operation: reply to / resolve / reopen a Drive file comment (Doc/Sheet/Slides) via `comments.replies.create`. Takes `file_id` + `comment_id` (now surfaced in each `comments.md` comment header as a trailing code-span) + `content` and/or `action` (`resolve`/`reopen`); agent replies auto-prefix `[agent] ` so humans can tell them apart. Stdio-only — it's a mutation, not in the remote-safe set (mise-tojuji)
- Pre-flight fetch input-shape diagnosis: `detect_fetch_input_problem()` catches the two shapes agents reliably fumble — a 12-char deposit-folder prefix reused as a file ID, and a non-fetchable URL (GitHub / arbitrary site / Gmail `#search`-or-inbox) — returning a teaching `invalid_input` error before the bare Google 404 (mise-dizupe)

### Fixed
- `do(draft)` / `do(reply_draft)` now render GFM tables and **bold** in the Gmail draft body. The old `<p>`/`<br>`-only path emitted literal `|---|` rows and `**` asterisks; content routes through `html_convert.markdown_to_html` (python-markdown, extensions `tables`/`nl2br`/`sane_lists`). `markdown` added to **core** deps — `draft`/`reply_draft` are remote-safe ops that must render in the slim build (mise-zolowa)

### Docs
- `do` verb documented as 15 ops (was 14) across CLAUDE.md + README, `comment_reply` added to `docs_do()` + `DO_DESCRIPTION_FULL`, and a `SKILL.md` "Replying to Comments" workflow + anti-patterns. `html_convert.py` utility entry updated to HTML↔markdown (both directions).

## [0.7.12] - 2026-06-20

### Docs
- Remove the decommissioned garde-manger composition bullet from `skills/mise/SKILL.md` (garde retired 2026-06-03). Part of a suite-wide post-cutover staleness sweep. SKILL.md is vendored, so the ratchet needs the bump.

## [0.7.11] - 2026-06-20

### Fixed
- `ensure-mise.sh` hook output is now valid JSON on failure: rendered via `python3 json.dumps` instead of a raw `cat <<EOF` heredoc, which left the quoted `"$PLUGIN_ROOT"` in the dependency/OAuth recovery messages unescaped and broke the emitted JSON. `uv sync` stderr is now captured to `~/.cache/mise/ensure.log` (was `2>/dev/null`), and a failed sync surfaces the log path. Found during bon-mavemi/bon-dotupu (same heredoc-quote bug as passe/todoist).

## [0.7.10] - 2026-06-17

### Docs
- CLAUDE.md: document the full vs slim build flavours (the `extraction` extra), `--extra extraction` for the test suite, and the slim PDF→Drive write-scope dependency (mise-hibere follow-on). Docs-only — CLAUDE.md is vendored content, so the ratchet needs the bump.

## [0.7.9] - 2026-06-17

### Changed
- `do(move)` target folder is now `folder_id` (canonical, matching `do(create)`); `destination_folder_id` kept as a deprecated alias (mise-kivane)
- Guest mode (`MISE_TOKEN_PATH` set): `search` with no explicit `sources` now defaults to `['drive']`, so a guest token with no Gmail scope no longer fails confusingly (mise-kivane)
- Local extraction (`markitdown[pdf]`, `pdf2image`) moved to an optional `extraction` extra; the plugin spawns with `uv run --extra extraction` so desktop/Cowork stay full. The embedded (Cornichon) flavour installs plain core and sheds magika→onnxruntime (~67M): PDF text falls back to Drive server-side conversion, HTML→markdown to tag-stripping, thumbnails skip, and image fetch (pillow, now core) keeps working (mise-hibere)

## [0.7.8] - 2026-06-11

### Fixed
- Guest-mode credential path: load ADC-shaped files directly, never write back; `MISE_TOKEN_PATH` override for caller-owned token files (mise-lebapo)
- Send `x-goog-user-project` when credentials carry a quota project (mise-lebapo)

## [0.7.7] - 2026-06-10

Packaging only — restores the mise skill to the marketplace package.
0.7.6's assembler excluded the deposit folder with an unanchored rsync
pattern (`mise/`), which also matched `skills/mise/` and silently ate
the skill; Desktop showed "this plugin doesn't have any skills". The
exclude is now root-anchored and a parity guard fails the build if a
source capability dir (skills/hooks/commands) goes missing from the
vendored package.

## [0.7.6] - 2026-06-10

Packaging only — no code change. The corrected marketplace assembly
(no repo-local .claude/, vendored uv.lock actually committed) needs a
version clients will re-resolve; 0.7.5's marketplace package was briefly
published with both flaws.

## [0.7.5] - 2026-06-10

Field-report fixes (reply_all cc, .docx flattening warnings), the gmail.py
structural split, and skill search guidance. Released same-day to carry the
marketplace husk fix: the Desktop/org assembler now vendors full MCP runtime
source, so this version is the first to arrive in Cowork *with its server*
(see notes/raw/2026-06-10-mise-cowork-husk-diagnosis.md).

### Changed
- **server.py refactored to the registration shim it always claimed to be** (mise-jimohe, 1,318 → 344 lines). No behaviour change. Resource text moved to `resources/docs.py`; remote orchestration (`search_remote`/`fetch_remote`, `REMOTE_ALLOWED_OPS`) to `tools/remote.py`; dispatch machinery (`DISPATCH`, `REQUIRED_PARAMS`, `run_operation()`, do() descriptions) to `tools/dispatch.py`. `_REMOTE_MODE` stays in server.py at module load (decoration-time constraint).
- `tests/unit/test_architecture.py` jurisdiction extended: `LAYER_RULES` now covers `workspace/` and `resources/`; new discovery-based `FILE_RULES` police every root-level .py (entry-point and retry.py exceptions documented); server.py capped at 500 lines.
- README/CLAUDE.md drift sweep (mise-lijogi): verb table now says do (14 ops, was create), status Stable (was Beta), auth flags corrected (`--auto`/`--code`; `--manual` never existed), adapter table gained calendar/forms/charts/cdp, caches enumerated, broken skill-section code fence fixed.
- **tools/fetch/gmail.py split along its three concerns** (mise-wugehi, 903 → 557 lines). No behaviour change. Participants extraction → `gmail_participants.py`; exfil matching → `gmail_exfil.py`; MIME resolution + attachment download/deposit → `gmail_attachments.py`. New `classify_attachment()` is the single source of MIME→category dispatch knowledge (previously triplicated across `_is_extractable_attachment`, `_deposit_attachment_content`, and fetch_attachment's branch chain). The file's 10 mypy name-reuse errors dropped to 0. Test patch targets for the moved helpers now point at `tools.fetch.gmail_attachments.*` (mechanical update, same pattern as the jimohe refactor). Verified live: thread fetch with participants and eager PDF extraction both exercised end-to-end post-split.

### Fixed
- `ContentType` Literal missing `"form"` (workspace/manager.py) — toise finding.
- Path containment in `do(create)` used `str.startswith` (prefix-collision admits `/repo-evil` siblings) — now `Path.is_relative_to`.
- `setup_oauth` leaked the parent's log file handle after spawning the detached auth subprocess.
- **reply_all built a wrong cc list** (mise-lurumu) — two stacked bugs. `_parse_headers` matched header names case-sensitively, so Outlook's `CC:` (uppercase) was silently dropped and externally-sent messages lost their cc list (also affected participants extraction); header names now canonicalise case-insensitively per RFC 5322. And `do_reply_draft` never passed the authenticated email into `_infer_recipients_all`, so self-exclusion was dead code and the user's own address landed in cc. Verified live against the field-report thread: To: sender, Cc: original cc only.

### Added
- **Fetched .docx warns when Word markup was flattened** (mise-kecigu MVP) — Drive's markdown export silently drops tracked changes (a tracked-DELETED clause reads as ordinary present text), Word comments, and inline images. The docx conversion path now inspects the original archive (pure regex-on-bytes counter in `extractors/docx_markup.py` — no XML parse, untrusted input can't entity-bomb it) and emits cue warnings naming counts, authors, and the remedy. Verified against the field-report document: 381 tracked changes by 3 authors + 25 comments now announced instead of silent. Known gap: the Gmail pre-exfil path (server-side copy, no local bytes) skips inspection.

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
