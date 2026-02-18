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
| **Overwrite: markdown → Docs with heading styles** | batchUpdate: delete all → insertText → updateParagraphStyle | Parses ATX headings (`#` through `######`), strips to plain text, inserts, then applies named styles at tracked positions. Positions counted in UTF-16 code units (not Python chars) because the Docs API uses UTF-16 indices — emoji and non-BMP characters are 2 units. `_utf16_len()` helper in `tools/overwrite.py`. Known limitation: only heading styles are applied; bold, italic, lists, links are inserted as plain text. |
| **Surgical edits: prepend/append/replace_text** | Three focused operations, no raw index | Raw index-based insert was rejected — Claude would need to count characters, which is fragile and error-prone. prepend (index 1), append (endIndex - 1), and replaceAllText cover practical use cases. `replace_text` uses the Docs API's native `replaceAllText` (case-sensitive, `find` parameter) and reports `occurrences_changed` in cues. Append inserts before the doc's trailing structural newline. |
| **Docs API: no supportsAllDrives needed** | Docs API works on all accessible docs | Unlike Drive API, the Docs API doesn't need `supportsAllDrives`. If the user has edit access, batchUpdate works regardless of whether the doc is on a Shared Drive. |
| **do() input signature: growing pains** | 11 optional params, most irrelevant per operation | `do()` routes 6 operations via a single MCP tool with `operation` param. Each operation uses a different subset of params (`content` means different things for create vs replace_text). Refactor candidate: per-operation param validation at the router. Response shape is normalised; input shape is not yet. |
| **ID auto-detection** | fetch(id) figures out type | Gmail thread IDs look different from Drive file IDs. Server detects, no explicit source param needed. |
| **Pre-exfil detection** | Check "Email Attachments" folder | User runs background extractor. Value isn't speed (Gmail is 3x faster); value is Drive fullText indexes PDF *content*. |
| **Sync adapters, async tools** | Adapters sync, tools can wrap | Google API client is synchronous. Adapters stay sync. For MCP v2 tasks (async dispatch), tools layer wraps with `asyncio.to_thread()`. Avoids rewriting adapters. |
| **Sheets: 2 calls not 1** | `get()` + `batchGet()` | `includeGridData=True` returns 44MB of formatting metadata vs 79KB for values-only. Benchmarked: 2 calls is 3.5x faster despite extra round-trip. |
| **Large file streaming** | 50MB threshold | Files >50MB stream to temp file instead of loading into memory. Prevents OOM on gigabyte PPTXs. Configurable via `MISE_STREAMING_THRESHOLD_MB` env var. |
| **No search snippets** | `snippet: None` | Drive API v3 has no `contentSnippet` field. The API returns 400 if requested. `fullText` search finds files but doesn't explain *why* they matched. |
| **Search deposits to file** | Path + counts, not inline JSON | Filesystem-first consistency with fetch. Claude reads deposited JSON when needed. Saves ~5% tokens per search but scales better (10 parallel searches = 30-40k tokens avoided). |
| **base_path is required** | No silent cwd fallback | MCP servers run as separate processes — `Path.cwd()` is their cwd, not Claude's. `base_path` is required on `search` and `fetch` (empty string → error). `workspace/manager.py` raises `ValueError` if `None`. Callers must pass their working directory explicitly. |
| **Web: trafilatura not Defuddle** | trafilatura (Python) | Best F1 score (0.883) in benchmarks, Python-native (no Node subprocess), battle-tested at scale. Defuddle (JS) preserves code hints better but requires Node. We work around trafilatura's code block mangling via pre-process/restore pattern instead of forking. |
| **Web: code block preservation** | Pre-process/restore | Extract `<pre>` blocks before trafilatura, replace with placeholders, restore after. Avoids forking trafilatura while preserving language hints. |
| **Web: passe browser fallback** | passe (CDP) replaces webctl (Playwright) | passe's `read` verb injects Readability.js + Turndown.js, returns markdown directly. `WebData.pre_extracted_content` carries the result; tool layer skips trafilatura when set. Three-tier SPA detection: short HTML, empty body text, framework patterns. |
| **Web: binary Content-Type routing** | Adapter captures raw bytes, tool routes by type | Web URLs that return `application/pdf` (or other binary types) are detected via Content-Type in the adapter, which captures `raw_bytes` on `WebData` and skips HTML inspection. Tool layer checks Content-Type and routes to the appropriate extractor. Status code checks (404, 429, 500) run *before* binary detection. Only types with working extractors are in `BINARY_CONTENT_TYPES` — don't add types we can't process. |
| **Web: 403/401 auto-fallback** | Try passe before raising AUTH_REQUIRED | When HTTP fetch gets 403/401, auto-retry via passe (Chrome's authenticated session) if available. Paywall detection (soft auth, 200 status) does NOT auto-fallback — passe can't bypass paywalls, only real session auth. |
| **Web: hostile site defences** | Redirect loops, size bombs, tarpits | `TooManyRedirects` caught explicitly. HTML capped at 10MB via `Content-Length` check. 30s timeout, CAPTCHA detection, 429/500+ handling, binary streaming >50MB. Known gap: servers omitting Content-Length bypass size check — timeout is the backstop. |
| **Web: extraction_failed constant** | `EXTRACTION_FAILED_CUE` shared between extractor and tool | Extractor writes the stub, tool matches it for cues. Shared constant in `extractors/web.py` prevents silent breakage if stub wording changes. Cross-layer test verifies both sides use the same constant. |
| **Workspace directory: mise/ not mise-fetch/** | Single bidirectional workspace | `mise/` is one station — fetch deposits there, do reads from there. Renamed from `mise-fetch/` (Feb 2026) because the deposit-then-publish pattern means the directory is bidirectional. |
| **Deposit-then-publish: source param** | `do(source="mise/...")` reads from deposit | Instead of passing content inline (burns tokens), caller writes to `mise/` deposit folder, then `do(source=path)` reads content.md (doc) or content.csv (sheet) from it. Title falls back to manifest.json. After creation, manifest is enriched with receipt. `_SOURCE_FILENAME` dict in `tools/create.py` maps doc_type → expected filename. |
| **Gmail: no inline attachment text** | Pointers, not inline content | Extracted attachment text goes to separate `{filename}.md` files. content.md gets compact `**Extracted attachments:**` summary with `→ \`filename.md\`` pointers. Inlining bloated content.md 10x and created truncation needs. |
| **Gmail: single-attachment fetch** | `fetch(thread_id, attachment="file.xlsx")` | Office files are skipped during eager thread extraction (5-10s each). The `attachment` parameter enables on-demand extraction of any specific attachment. Routes through same extractors as Drive files. Pre-exfil Drive copies checked first. |
| **Gmail: forwarded message preservation** | Split-then-strip pipeline + MIME rfc822 parsing | `strip_signature_and_quotes()` was destroying forwarded messages. Fix: `split_forward_sections()` detects forward markers *before* stripping. Separately, `parse_forwarded_messages()` walks MIME tree for `message/rfc822` parts. Two independent paths: inline forwards (plain text markers) and MIME-attached forwards (binary parts). |
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
