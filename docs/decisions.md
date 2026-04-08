# Architectural Decision Record

Full history of design decisions, benchmarks, and implementation details. Moved from CLAUDE.md (Feb 2026) to reduce per-session token cost. Read this when you need to understand *why* something was built a certain way.

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **No `purpose` parameter** | Always LLM-analysis | This MCP is Claude's sous chef — always preparing for LLM consumption. Archival/editing modes are YAGNI. |
| **PDF: hybrid extraction** | markitdown → Drive fallback | Try markitdown first (fast, MIT). Two fallback triggers: (1) <500 chars extracted, (2) structural quality gate — `_looks_like_flattened_tables()` detects data-heavy PDFs where markitdown produces enough chars but no row/column structure (three-signal: short_ratio ≥0.60, sentence_ratio ≤0.10, numeric_ratio ≥0.15). Benchmarked: Drive extracts 100-1000x more content from complex PDFs. PyMuPDF tested but offers no quality advantage over markitdown and has AGPL license. |
| **PDF thumbnails: always-on, platform-adaptive** | CoreGraphics (macOS) → pdf2image (Linux) | Benchmarked Feb 2026: CG 5.7ms/page, pdf2image 83ms/page, Chrome CDP 5s/page (rejected), PyMuPDF 17ms/page (rejected — AGPL), Drive thumbnail page-1 only (rejected). Always render all pages (capped at 100). Thumbnails are additive — rendering failure = warning, not error. Sized to ~1568px longest side (Anthropic vision API max). macOS uses per-page DPI via CoreGraphics; Linux uses fixed 150 DPI (slight overshoot, API downscales). `poppler-utils` is a system dependency; if missing, text extraction still works. `render_pdf_pages()` is NEVER called inside `extract_pdf_content()` — rendering is I/O, called by `fetch_and_extract_pdf()` (Drive path) or tool layer (web path). |
| **3 verbs not 17 tools** | search, fetch, do | v1 had 17 tools. Claude doesn't need that many levers. Unified search + polymorphic fetch covers 95% of use cases. The 3rd verb is `do(operation=...)` — routes via operation param. |
| **Move: single-parent enforcement** | Remove all parents, add destination | Google Drive technically supports multi-parent but it causes confusion. Move is a true move, not "add another parent". Validates destination is a folder (MIME type check) before attempting — clear `INVALID_INPUT` error if not. |
| **Overwrite: Drive import instead of Docs API** | `files().update()` with `text/markdown` media type | Originally used Docs API batchUpdate (delete → insertText → apply heading styles) which only rendered headings — bold, tables, lists were plain text. Verified Mar 2026 that `files().update()` with `text/markdown` triggers the same import conversion as `files().create()`. All markdown formatting renders automatically. Replaced ~120 lines (heading parsing, UTF-16 position tracking, style application) with a single `upload_file_content()` call. |
| **Surgical edits: prepend/append/replace_text** | Three focused operations, no raw index | Raw index-based insert was rejected — Claude would need to count characters, which is fragile and error-prone. prepend (index 1), append (endIndex - 1), and replaceAllText cover practical use cases. `replace_text` uses the Docs API's native `replaceAllText` (case-sensitive, `find` parameter) and reports `occurrences_changed` in cues. Append inserts before the doc's trailing structural newline. |
| **Plain file editing: dispatch-level metadata** | Pre-fetch metadata once at `do()` dispatch, pass to handlers | Content operations (overwrite, prepend, append, replace_text) need file MIME type to route: Google Docs → Docs API, plain files → Drive Files API (download-modify-upload). Three routing options considered: (1) each handler fetches its own metadata — adds an extra API call per edit on the Google Doc happy path; (2) try Docs API first, catch 400, fall back — makes plain file ops slower (failed call + retry); (3) dispatch pre-fetches once, handlers share via `metadata=` param. Option 3 chosen: zero extra API calls, clean routing. `metadata=None` (direct call without dispatch) falls through to Google Doc path for backward compatibility. Google Workspace types (Sheets/Slides) that reach the plain file path are rejected with a clear error — `get_media()` doesn't work on native types. Structured formats (SVG, JSON, XML) get `cues.structured_format=true` on prepend/append to warn Claude that string concatenation may break structure. |
| **Plain file creation: doc_type='file'** | Upload as-is via `MediaInMemoryUpload`, no Google conversion | `create` previously only made Google Docs/Sheets/Slides (conversion upload). `doc_type='file'` skips conversion — MIME type inferred from title extension via `mimetypes.guess_type()` with fallback dict for `.md`, `.yaml`, `.yml`, `.toml`. Falls back to `text/plain` for unknown extensions. `source` param not supported — the deposit-then-publish pattern reads specific filenames per doc_type (`content.md` for doc, `content.csv` for sheet). For `file`, there's no canonical filename convention; the content file could be anything. Adding a naming convention or glob pattern adds complexity for a use case that's currently met by inline content. Revisit if large plain files need deposit-then-publish. Cues include `plain_file: true` and `mime_type` matching the edit operations' shape. Discovered during integration testing: Claude had to hack around mise with raw Drive API to create a test file — if the tool can edit plain files, it should create them too. |
| **httpx migration: two-phase (sync first, async later)** | Phase 1: `httpx.Client` (sync) via `MiseSyncClient`; Phase 2: `httpx.AsyncClient` via `MiseHttpClient` | Making one adapter async forces the entire chain async (adapter → tools → server.py → FastMCP). During a multi-session migration with 8 adapters, you'd need `asyncio.to_thread()` wrappers for every not-yet-migrated adapter — messy and error-prone. Phase 1 keeps adapters sync (callers unchanged, tests simple) while gaining orjson, connection pooling, no discovery doc overhead, and h2. Phase 2 is a single-shot conversion: all adapters done → make tools/server async → switch to `MiseHttpClient` → delete `MiseSyncClient` → restructure for real concurrency (`asyncio.gather()` for metadata+comments, search sources, thumbnails). `MiseSyncClient` is intentional duplication — don't "clean it up" by deleting it before Phase 2. |
| **Docs API: no supportsAllDrives needed** | Docs API works on all accessible docs | Unlike Drive API, the Docs API doesn't need `supportsAllDrives`. If the user has edit access, batchUpdate works regardless of whether the doc is on a Shared Drive. |
| **do() input signature: growing pains** | 21 optional params, most irrelevant per operation | `do()` routes 13 operations via a single MCP tool with `operation` param. Each operation uses a different subset of params (`content` means different things for create vs replace_text). Refactor candidate: per-operation param validation at the router. Response shape is normalised; input shape is not yet. |
| **Tool descriptions must stay under 2048 chars** | Short descriptions, detail in `mise://tools/*` resources | CC's `MAX_MCP_DESCRIPTION_LENGTH` (2048ch, `client.ts:218`) truncates MCP tool descriptions. Truncated descriptions cause the Anthropic API to silently drop properties from the tool schema during `tool_reference` expansion (deferred tool loading). Discovered Apr 2026 when `page_setup` and `tabs` params were invisible to Claude despite being in the server schema. Fix: `_DO_DESCRIPTION_FULL` shortened from 2494→~600ch. Full documentation lives in `mise://tools/do` resource. Don't re-expand descriptions without checking total size. |
| **Folder creation: early return in do_create** | `doc_type='folder'` exits before content validation | Folders need only a title — no content, source, or file_path. Rather than adding folder to `_do_create_internal`'s valid_types and threading "no content required" through the validation chain, `do_create` intercepts early and calls `_create_folder` directly. `supportsAllDrives=true` is set on the API call for Shared Drive compat. |
| **Heading blockquote suppression** | Headings skip blockquote detection | Indented paragraphs (≥30pt) get blockquote prefix (`>`). But headings with indentation (common in numbered-heading Google Docs) were rendered as `> > # 1. Section`. Fix: `not heading_prefix` added to the blockquote condition. Headings are structurally distinct from indented body text. |
| **ID auto-detection** | fetch(id) figures out type | Gmail thread IDs look different from Drive file IDs. Server detects, no explicit source param needed. |
| **Pre-exfil detection** | Check "Email Attachments" folder | User runs background extractor. Value isn't speed (Gmail is 3x faster); value is Drive fullText indexes PDF *content*. |
| **Sync adapters, async tools** | Adapters sync, tools can wrap | Google API client is synchronous. Adapters stay sync. For MCP v2 tasks (async dispatch), tools layer wraps with `asyncio.to_thread()`. Avoids rewriting adapters. |
| **Sheets: 2 calls not 1** | `get()` + `batchGet()` | `includeGridData=True` returns 44MB of formatting metadata vs 79KB for values-only. Benchmarked: 2 calls is 3.5x faster despite extra round-trip. |
| **Large file streaming** | 50MB threshold | Files >50MB stream to temp file instead of loading into memory. Prevents OOM on gigabyte PPTXs. Configurable via `MISE_STREAMING_THRESHOLD_MB` env var. |
| **No search snippets** | `snippet: None` | Drive API v3 has no `contentSnippet` field. The API returns 400 if requested. `fullText` search finds files but doesn't explain *why* they matched. |
| **Search deposits to file** | Path + counts, not inline JSON | Filesystem-first consistency with fetch. Claude reads deposited JSON when needed. Saves ~5% tokens per search but scales better (10 parallel searches = 30-40k tokens avoided). |
| **base_path is required** | No silent cwd fallback | MCP servers run as separate processes — `Path.cwd()` is their cwd, not Claude's. `base_path` is required on `search` and `fetch` (empty string → error). `workspace/manager.py` raises `ValueError` if `None`. Callers must pass their working directory explicitly. |
| ~~**Web: trafilatura not Defuddle**~~ | ~~trafilatura (Python)~~ | ~~REMOVED (Mar 2026): Web fetching moved to passe. Historical record only.~~ |
| ~~**Web: code block preservation**~~ | ~~Pre-process/restore~~ | ~~REMOVED (Mar 2026): Web fetching moved to passe. Historical record only.~~ |
| ~~**Web: passe browser fallback**~~ | ~~passe (CDP) replaces webctl (Playwright)~~ | ~~REMOVED (Mar 2026): Web fetching moved to passe. Historical record only.~~ |
| ~~**Web: binary Content-Type routing**~~ | ~~Adapter captures raw bytes, tool routes by type~~ | ~~REMOVED (Mar 2026): Web fetching moved to passe. Historical record only.~~ |
| ~~**Web: 403/401 auto-fallback**~~ | ~~Try passe before raising AUTH_REQUIRED~~ | ~~REMOVED (Mar 2026): Web fetching moved to passe. Historical record only.~~ |
| ~~**Web: hostile site defences**~~ | ~~Redirect loops, size bombs, tarpits~~ | ~~REMOVED (Mar 2026): Web fetching moved to passe. Historical record only.~~ |
| ~~**Web: extraction_failed constant**~~ | ~~`EXTRACTION_FAILED_CUE` shared between extractor and tool~~ | ~~REMOVED (Mar 2026): Web fetching moved to passe. Historical record only.~~ |
| **Workspace directory: mise/ not mise-fetch/** | Single bidirectional workspace | `mise/` is one station — fetch deposits there, do reads from there. Renamed from `mise-fetch/` (Feb 2026) because the deposit-then-publish pattern means the directory is bidirectional. |
| **Deposit-then-publish: source param** | `do(source="mise/...")` reads from deposit | Instead of passing content inline (burns tokens), caller writes to `mise/` deposit folder, then `do(source=path)` reads content.md (doc) or content.csv (sheet) from it. Title falls back to manifest.json. After creation, manifest is enriched with receipt. `_SOURCE_FILENAME` dict in `tools/create.py` maps doc_type → expected filename. |
| **Gmail: no inline attachment text** | Pointers, not inline content | Extracted attachment text goes to separate `{filename}.md` files. content.md gets compact `**Extracted attachments:**` summary with `→ \`filename.md\`` pointers. Inlining bloated content.md 10x and created truncation needs. |
| **Gmail: single-attachment fetch** | `fetch(thread_id, attachment="file.xlsx")` | Office files are skipped during eager thread extraction (5-10s each). The `attachment` parameter enables on-demand extraction of any specific attachment. Routes through same extractors as Drive files. Pre-exfil Drive copies checked first. |
| **Gmail: forwarded message preservation** | Split-then-strip pipeline + MIME rfc822 parsing | `strip_signature_and_quotes()` was destroying forwarded messages. Fix: `split_forward_sections()` detects forward markers *before* stripping. Separately, `parse_forwarded_messages()` walks MIME tree for `message/rfc822` parts. Two independent paths: inline forwards (plain text markers) and MIME-attached forwards (binary parts). |
| **Gmail write: draft-only, no send** | Claude drafts, user reviews and sends | Sous-chef philosophy: Claude prepares, user serves. Draft-only is the safe default even for a hypothetical Claude-owned email account (who logs in to hit send?). `gmail.modify` scope (superset of `gmail.readonly`) covers drafts and unlocks future label/archive ops without a second re-auth. `gmail.compose` was rejected — too narrow, would require another re-auth when label/archive lands. |
| **Gmail write: always links, never MIME attachments** | `include` param takes Drive file IDs → formatted links in body | Design conversation explored three strategies: (1) MIME attachments for local files, Drive links for cloud files — two mechanisms, complex. (2) Always upload to Drive first, then link — one mechanism but forces a Drive upload step. (3) Always links, Drive IDs only — simplest. Chose (3): if the file is in Drive, link it. If it's local, Claude uploads via `do(operation="create")` first — that path already exists. One mechanism, no 25MB attachment limit, files persist and are searchable. The `_resolve_include()` function currently expects Drive file IDs only; if we later want to accept mise deposit paths, the param type stays the same but resolution logic would need path-vs-ID detection. |
| **Gmail write: reply threading** | Separate `reply_draft` operation, not a flag on `draft` | Cold compose and threaded reply are different enough to warrant separate operations: reply needs `In-Reply-To`/`References` headers, recipient inference from thread, and `threadId` in the API call. Build order: `draft` first (mise-jiseti, done), `reply_draft` second (mise-bolaba), archive/label/star third (mise-safila, whenever it itches). |
| **Gmail write: HTML body is simple** | Paragraph splitting + `<br>`, no markdown rendering | `_content_to_html()` intentionally doesn't render markdown formatting — `**bold**` shows literally in HTML part. Email, not document. The text/plain part is always present as fallback. Markdown-to-HTML conversion is a future enhancement if needed, not a bug. |
| **Gmail write: draft web link construction** | `https://mail.google.com/mail/#drafts/{draft_id}` | Assumes default Gmail web UI. Workspace domains with custom URL patterns may not resolve correctly. Low risk — the draft is always in Drafts regardless of whether the link works. |
| **Activity API: two target types** | `_parse_target` handles both `driveItem` and `fileComment.parent` | Comment activities use `fileComment` target (with drive item nested in `parent`), non-comment activities use `driveItem`. Both have the same shape. |
| **Activity API: people IDs not names** | `personName` → `people/ID` treated as Unknown | Activity API returns opaque `people/ID` strings, not display names. No workaround — the API doesn't expose names for privacy. |
| **Skill: MANDATORY for all operations** | Gate on every fetch/search/create | Evidence (Feb 2026): test Claudes skip skill and go straight to MCP tools — keyword soup in Gmail, miss comments, forget base_path. MANDATORY gate needed. |
| **Fetch: cues in response** | Surface decision-tree signals inline | Blind test: test Claude skipped manifest.json due to momentum. Fix: `cues` block in every fetch response. ~25-70 tokens. |
| **Search: preview in response** | Top 5 results per source inline, full set on disk | Prevents field-name guessing. Drive adds `email_context`, Gmail adds `message_count` and `attachment_names`. ~200-300 tokens. |
| **XLSX: Sheets API not CSV export** | Upload+convert, read via Sheets API, delete temp | Drive CSV export only returns the first sheet of a multi-tab spreadsheet. Fix: upload+convert to temp Google Sheet, read all tabs via Sheets API, delete temp. |
| **Per-tab CSV deposits** | `content_{tab_slug}.csv` per tab, combined `content.csv` kept | A Claude missed that a single CSV contained multiple tabs. Per-tab files are unambiguous. Only written for multi-tab spreadsheets (2+ tabs). `extract_sheets_per_tab()` produces per-tab data; `_write_per_tab_csvs()` writes them. |
| **Multi-tab sheet creation: hybrid path** | CSV upload for tab 1, Sheets API for additional tabs | Tab 1 uses CSV upload (fast). Additional tabs via `add_sheet()` + `update_sheet_values()` with `USER_ENTERED` input option (preserves formulae). |
| **XLSX: raw file deposit** | Original `.xlsx` alongside CSV | CSV is lossy. Tool layer writes original file to deposit. Cues surface it; `formula_count` tells callers when the raw file matters. |
| **CSV tick-prefix: display hint not data** | Sheets API returns plain value | Tick-prefix (`'00412`) is a display hint. `values().get()` returns `"00412"`, not `"'00412"`. Round-tripping loses the tick, but the value survives. |
| **Image: resize not skip** | `resize_image_bytes()` at 1568px long edge | Images exceeding 1568px are resized rather than skipped. Anthropic's API downscales internally above this threshold anyway — pre-resizing costs nothing in quality and avoids silent omission. Format is preserved (JPEG stays JPEG, PNG stays PNG). PNG→JPEG fallback only if PNG is still >4.5MB post-resize (rare). Only genuine PIL failures (bytes aren't an image at all) cause a skip. Pre-download `att.size` check removed — oversized images are downloaded and resized. Format check retained: unsupported MIME types (not jpeg/png/gif/webp) still skip pre-download because the format itself is the blocker. |
| **Deposit: wipe on re-fetch** | `get_deposit_folder()` clears existing files | Re-fetching the same resource (same type + title + ID → same folder name) previously left ghost files from the prior fetch alongside the new content. Fix: `get_deposit_folder()` iterates the folder and unlinks all files before returning it. Subdirectories untouched (rare). New folders: iterdir is a no-op. Risk: do NOT call `get_deposit_folder` twice for the same folder mid-operation — the second call wipes files the first produced. |

| **Calendar: thin adapter not gws MCP** | `adapters/calendar.py` using google-api-python-client | gws MCP server tested (Mar 2026): registers 37 tools for calendar (full Discovery surface), generic `params: object` schemas with no validation, destructive ops (`calendars_clear`) alongside reads, stdio pipe fragile. CLI is fine for ad-hoc exploration (~0.5s). But for production: thin adapter (~60 lines) is consistent with mise architecture, testable with fixtures, no binary dependency. Calendar data shape confirmed: `summary`, `attendees[]` (name/email/responseStatus), `hangoutLink`, `conferenceData`, `description` (HTML), `attachments[].fileUrl` (Drive docs), `eventType` for filtering. |
| **Remote: Tailscale Funnel over Cloudflare Tunnel** | Tailscale Funnel | tailscaled already on kube — Funnel is fewer moving parts, no extra account. Trade-off: 100 conn/min rate limit and 1MB/s throughput cap. Acceptable for single-user; test with representative payloads (large inline doc fetches). Decided Mar 2026. |
| **Remote: single-user only** | One `token.json`, one `lru_cache` per service | Explicitly confirmed as a design choice, not a gap. `lru_cache(maxsize=1)` on service getters in `adapters/services.py` is fundamentally single-tenant. Multi-tenancy would require per-request credential injection (architecturally significant). Security boundary is application-level operation gating (`_REMOTE_ALLOWED_OPS`), not Google OAuth scopes. Decided Mar 2026. |
| **Remote: httpx migration before containerisation** | Sync adapters block event loop under concurrent load | `googleapiclient` is sync-only — remote server wraps via `asyncio.to_thread()`, hitting Python's default `ThreadPoolExecutor` limit under concurrent Kube load. This is a scalability ceiling for a production server, not just a nice-to-have refactor. Migration must land before containerisation. Decided Mar 2026. |
| **Remote: MISE_REMOTE=1 env var for containers** | Env var over `--remote` flag | `_REMOTE_MODE` checks both `sys.argv` and env var. `sys.argv` detection is fragile under process managers (Gunicorn, WSGI adapters, `-m` invocation). Containers should use `MISE_REMOTE=1` exclusively. The early-evaluation pattern (`_REMOTE_MODE` at module load) is intentional — `@mcp.tool(description=...)` fires at decoration time. Decided Mar 2026. |

## Per-Service API Patterns

| Service | Optimal Pattern | Calls | Why |
|---------|-----------------|-------|-----|
| **Docs** | `get(includeTabsContent=True)` | 1 | Minimal overhead, all tabs in one response |
| **Sheets** | `get()` + `values().batchGet()` | 2 | `includeGridData` bloats payload 560x with formatting metadata |
| **Slides** | `get()` + concurrent `getThumbnail()` | 1+N | Batch not supported, but concurrent with isolated services works (3.2x faster at 2 workers). Google rate-limits at 3+ concurrent calls. Each thread needs its own service object — shared httplib2 causes SSL corruption. |
| **Gmail** | `threads().get()` + batch `messages().get()` | 2 | Thread metadata + full message bodies |

## Timing & Benchmarks

**Slides (Jan 2026):** HTTP batch requests are NOT supported for Workspace editor APIs — Google disabled this in 2022. Concurrent individual `getThumbnail` requests work with isolated service objects (one per thread), capped at 2 workers. 3.2x faster for 43 slides (22s vs 71s).

**Office conversion (Feb 2026):** Upload+convert dominates at 67-77% of total time (DOCX: 5.7-7.0s, XLSX: 4.4-4.9s). Server-side conversion inside `files().create()` — nothing to optimise. The `source_file_id` copy path skips download+upload entirely.

**Selective thumbnails (Jan 2026):** Enabled by default because selective logic makes them cheap. Extractor analyzes each slide: charts (visual IS content), images (unless stock photo >50%), fragmented text (≥5 short pieces). Text-only slides and stock photos skipped.

## Linked Content in Docs

| Source | What API exposes | What we output |
|--------|------------------|----------------|
| **Sheets chart** | `linkedContentReference.sheetsChartReference` with spreadsheet/chart ID | `[Chart: title (from spreadsheet X)]` |
| **Sheets table** | Native table structure (not a linked object) | Markdown table |
| **Slides** | Image only, `linkedContentReference: {}` (empty) | `![image](url)` |

**Slides link limitation:** The Docs API doesn't expose the source presentation ID for linked slides. Known limitation.

**inlineObjects is per-tab:** In multi-tab docs, `inlineObjects` lives at `documentTab.inlineObjects`, not at document level. The model reflects this: `DocTab.inline_objects`.

## Docs API Element Taxonomy

ParagraphElement types:
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

## Comments Detail

Comments are fetched via `fetch_file_comments()` in `adapters/drive.py` and formatted via `extract_comments_content()` in `extractors/comments.py`.

**What's captured:** Author name/email, content, creation date, resolved status, quoted text (anchor), threaded replies.

**File types that don't support comments:**

| Type | API Behavior |
|------|--------------|
| Folders | Returns 0 comments (no error) |
| Forms | 404 → `MiseError(INVALID_INPUT, "Comments not supported for form files")` |
| Shortcuts | 404 → same error (doesn't resolve to target) |
| Sites, Maps, Apps Script | Same 404 pattern |

The adapter pre-checks known unsupported MIME types before hitting the API.

## Unsupported Content Types

| Content Type | Why Unsupported | Alternatives |
|--------------|-----------------|--------------|
| **Google Groups** | No read API exists. Groups Migration API is write-only. Web scraping ~3s per topic. | Vault export from IT (requires license), or Gmail subscriptions (incomplete). |

Confirmed with Jay Lee (GAM creator, Jan 2026) that Google has never provided a Groups content read API.
