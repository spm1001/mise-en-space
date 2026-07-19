# CLAUDE.md

**mise-en-space** — Google Workspace MCP (Drive, Gmail) with mise-en-place philosophy: everything prepped, in its place, ready for Claude to cook with.

## Versioning & releasing (suite-managed)

mise ships as part of the **Batterie de Savoir** suite, which carries **one suite-wide version**. So:

- **Do NOT hand-bump `.claude-plugin/plugin.json` to release.** This repo's own `plugin.json` version is **local-dev-only** — the assembler stamps every published plugin to the suite version, overwriting it.
- **Release via `/batterie:publish`** from this working tree — it bumps the suite version centrally and ships the change (a 2-repo push: this repo + the central suite bump). Never hand-run the assemble.
- **mise is vendored full-source** (it's the MCP plugin) — so *any* source edit here, plus `CLAUDE.md` / `instructions.md` / `skills/` / `hooks/`, is vendored content that must ride a suite bump (a publish) to ship, or the assembler quarantines the plugin. `docs/` / `.bon/` / `tests/` edits are free (excluded from the vendor).

Full picture: `spm1001/batterie-de-savoir` → `CLAUDE.md` "Versioning convention" + `.bon/understanding.md`.

### Identity flavours (mise vs mise-home) — distinct from build flavours

Mise ships as **two identity flavours** from this one source: `mise` (work — `mit-workspace-mcp-server` OAuth client, `@itv.com`) and `mise-home` (personal/family — `planetmodha-workspace-mcp` client, `@planetmodha.com`). This is a *different axis* from the full/slim build flavours (see Development → Build flavours). The home flavour is produced by `spm1001/batterie/transforms/make-mise-flavour.sh`, a **guarded substitution transform** that rewrites the identity strings in the vendored copy:

- the data-dir name, keychain service, and `rules/mise.md` filename in `hooks/*.sh`, plus the `mise-batterie-de-savoir` / `mise-oauth-token` constants in `oauth_config.py`;
- the `plugin.json` **`identity`** (`"ITV (itv.com)"`→`"Planet Modha (planetmodha)"`) and **`displayName`** fields — the base values live in the *source* `plugin.json` (drift-exempt from the ratchet), the transform overrides `identity` for home and a field-specific guard asserts the swap landed;
- the **skill's tool-prefix** `mcp__mise__`→`mcp__<name>__` (so the home skill's `allowed-tools` point at `mcp__mise-home__*`, not the work server) and a flavour marker front-loaded into the skill *description*;

and swaps `credentials.json`. **If you edit those identity strings in the hooks, `oauth_config.py`, the `plugin.json` identity/displayName, or the skill's `mcp__mise__` tool refs, know they get rewritten per-flavour and the transform's guard will fail the build on any un-rewritten `mcp__mise__` leftover or missing identity.** Full topology (three repos) + the coexistence-clarity work: `.bon/understanding.md` → "Identity flavours" — shipped suite 1.8.7.

## Architecture

```
extractors/     Pure functions, no MCP awareness (testable without APIs)
adapters/       Thin Google API wrappers (easily mocked)
tools/          MCP tool definitions + dispatch/remote orchestration (the wiring layer)
workspace/      File deposit management (.mise/ in cwd)
resources/      MCP resource text (mise://docs/*) + tool-doc registry
server.py       FastMCP registration shim (stdio default, --remote for StreamableHTTP) — ≤500 lines, enforced
apps-script/    Google Apps Script for email attachment extraction (runs in Google, not Python)
docs/           Design documents and references
```

**Shared utilities (root level)** — infrastructure that multiple layers need but doesn't belong in any single layer:

| File | Purpose | Used by |
|------|---------|---------|
| `html_convert.py` | HTML↔markdown: HTML→markdown via markitdown (needs tempfile — why it's not in extractors); markdown→HTML via python-markdown (`markdown_to_html`, for email draft bodies) | adapters, tools |
| `filters.py` | Attachment filtering logic (`is_trivial_attachment`, `filter_attachments`) — holds two `lru_cache`s | adapters, tools |
| `validation.py` | ID/URL validation (`validate_drive_id`, `validate_gmail_id`, etc.) | tools, adapters |
| `retry.py` | Retry decorator with exponential backoff and jitter | adapters |
| `logging_config.py` | Structured logging setup (`logger`, `log_retry`) | everywhere |
| `cues_util.py` | Identity cues (`with_identity`, `current_user_email`) — Protocol-typed, deliberately no adapter import | tools, models |

**Caches, enumerated** (no single authority — know all three): manual client singletons in `adapters/http_client.py` (`get_http_client`/`get_sync_client`, one per mode), two `lru_cache`s in `filters.py`, and a manual metadata cache in `adapters/drive.py` (~L579).

**Key references:** `docs/information-flow.md` (flow diagrams, timing data), `docs/decisions.md` (full design decision history with rationale).

**Layer rules:**
- Extractors NEVER import from adapters or tools (no I/O, no tempfile, no os)
- Adapters NEVER import from tools
- Adapters MAY import parsing utilities from extractors
- Adapters use `convert_*` names, not `extract_*` (extract_* reserved for pure extractors/)
- Tools wire adapters → extractors → workspace. The do() machinery (`DISPATCH`, `REQUIRED_PARAMS`, `run_operation`) lives in `tools/dispatch.py`; remote orchestration in `tools/remote.py`
- server.py registers tools/resources and holds the thin @mcp.tool wrappers — nothing else (capped at 500 lines)
- Shared utilities live at root level — they sit BELOW the layers and never import upward (retry.py's `adapters.http_client` import is the one documented exception)
- ALL of the above is mechanically enforced by `tests/unit/test_architecture.py` (`LAYER_RULES` for directories, `FILE_RULES` for server.py + root utilities). When adding a module tier, extend the rules — unpoliced tiers are where mass accumulates (server.py hit 1,318 lines before mise-jimohe)

### Adapter Specializations

| Adapter | Purpose |
|---------|---------|
| `drive.py` | File metadata, search, download, export, comments |
| `docs.py` | Google Docs API (multi-tab support) |
| `sheets.py` | Sheets API (batchGet for values) |
| `slides.py` | Slides API + thumbnail fetching |
| `gmail.py` | Gmail threads and messages |
| `activity.py` | Drive Activity API v2 |
| `calendar.py` | Calendar events with meeting context (attendees, attachments, Meet links); `get_event_by_ical_uid` for live invite-state (`showDeleted=true` load-bearing) |
| `forms.py` | Google Forms API v1 (structure: questions, sections, options) |
| `charts.py` | Sheets chart export via temporary Slides embed (Sheets API has no direct export) |
| `cdp.py` | Chrome DevTools Protocol cookie access (for genai.py; graceful fallback) |
| `conversion.py` | **Shared** Drive upload→convert→export→delete pattern |
| `pdf.py` | PDF conversion (hybrid: markitdown → Drive fallback) |
| `office.py` | Office file conversion (DOCX/XLSX/PPTX via Drive) |
| `image.py` | Image files (raster + SVG→PNG rendering) |
| `genai.py` | Video summaries via internal GenAI API (requires chrome-debug) |

## MCP Tool Surface (3 verbs)

| Tool | Purpose | Writes files? |
|------|---------|---------------|
| `search` | Find files/emails/activity/calendar events, return metadata + inline preview | No |
| `fetch` | Download content to `.mise/` in cwd, return path + cues | Yes |
| `do` | Act on Workspace — 16 ops (create, move, rename, share, overwrite, prepend, append, replace_text, draft, reply_draft, archive, star, label, comment, comment_reply, setup_oauth) | Varies |

**Key behaviors:**
- `search` returns metadata only — Claude triages before fetching
- `search` accepts `type=` for MIME filter: `folder`, `doc`, `spreadsheet`/`sheet`, `slides`, `pdf`, `image`, `video`, `form`. `query` is optional when `type` or `folder_id` is set.
- `search` gmail results carry `has_invite` (thread has a calendar invite — free, from the parts mask) so triage can spot meetings
- `fetch` auto-detects ID type (Drive file ID vs Gmail thread ID)
- `fetch` of a Gmail invite adds `cues.invite_state` — the **live** Calendar state (status/my_response/current_start/cancelled_at) resolved by iCalUID, not the email's frozen snapshot; a cancelled meeting also emits a warning. Best-effort (skipped silently without calendar scope). See `docs/2026-07-07-meduto-invite-event-state.md`.
- `fetch` accepts optional `attachment` param for extracting specific Gmail attachments
- `fetch` accepts optional `tabs` param (list of tab names) to fetch only specific tabs from spreadsheets
- `fetch` accepts `recursive=True` on folder IDs — returns full indented tree (max depth 5, 1000 items)
- `do` routes via `operation` param — `do(operation="create", ...)`
- `do(move)`, `do(archive)`, `do(star)`, `do(label)` accept `file_id` as `str | list[str]` for batch operations — returns per-thread/file summary with `succeeded`/`failed` counts
- `do(create)` and `do(overwrite)` accept `file_path` to read content directly from a local file — no deposit folder needed. For `doc_type='file'`, reads as binary; for `doc`/`sheet`, reads as UTF-8 text. Mutually exclusive with `content` and `source`.
- `do(create)` accepts `doc_type='folder'` — creates a Drive folder (title only, no content needed). `supportsAllDrives` is set automatically for Shared Drive compatibility.
- `do(create)` accepts `doc_type='form'` — creates a Google Form from a YAML or JSON spec. Uses Forms API v1 (not Drive), so `folder_id`, `source`, and `file_path` are ignored. The `content` param is the spec with `title`, `description`, and `questions` array. Supported question types: `paragraph`, `short_answer`, `checkboxes`, `multiple_choice`, `dropdown`, `scale`, `text`, `section_break`. Returns form edit URL and responder URL in cues.
- `do(create)` accepts `page_setup='pageless'` (doc_type='doc' only) — sets pageless mode via Docs API after creation.
- `do(create)` with `doc_type='doc'` auto-embeds local images: `![alt](local/path.png)` in markdown triggers post-creation Docs API injection. Requires brief public sharing of each image via Drive permissions — may be blocked by enterprise DLP policies. Check `cues.image_errors` for failures.
- `do(move)` accepts `file_id` as a list for batch moves — validates destination once, returns per-file summary. The target folder is `folder_id` (canonical, shared with `do(create)`); `destination_folder_id` is kept as a deprecated alias.
- **Comments included automatically** — open comments deposited as `comments.md`
- **Cues in every response** — `cues` block surfaces files, comment count, warnings, email context
- `base_path` is required on all tools in stdio mode — MCP servers run as separate processes, `Path.cwd()` is theirs not Claude's. In remote mode, `base_path` is optional (temp dir used automatically).

## Remote Mode

`server.py --remote` (or `MISE_REMOTE=1`) runs as a StreamableHTTP server on `/mcp` for Claude.ai custom connectors. Key differences from stdio:

| Aspect | stdio (default) | remote (`--remote`) |
|--------|----------------|---------------------|
| Transport | stdin/stdout | StreamableHTTP on `/mcp` |
| `do()` operations | All 16 | 6 safe ops: create, draft, reply_draft, archive, star, label |
| Content delivery | Filesystem deposits | Inline in JSON-RPC response (`content` + `comments` fields) |
| `base_path` | Required | Optional (temp dir) |
| Tool description | Full | Restricted (only safe ops + relevant params) |
| Health endpoint | N/A | `/health` returns `{"status": "ok"}` |

**Architecture:** `_REMOTE_MODE` is determined at module load time (before `@mcp.tool()` decorators run) so tool descriptions adapt. This is intentional — argparse validates in `__main__` but the value must be available earlier for the conditional `description=` parameter on `@mcp.tool()`. Don't move this to argparse without understanding why it's early.

**Operation gating:** `REMOTE_ALLOWED_OPS` in `tools/remote.py` (the gate itself fires in server.py's do() wrapper). Rejected ops get a generic "not available in remote mode" error listing only allowed ops — restricted op names are not leaked.

**Binary content:** Image fetches in remote mode return metadata and cues but no inline content (binary can't be text-encoded). A cue warning explains this.

## Error Handling

Errors are `MiseError` (in `models.py`) with `ErrorKind`: `AUTH_EXPIRED`, `NOT_FOUND`, `PERMISSION_DENIED`, `RATE_LIMITED`, `NETWORK_ERROR`, `INVALID_INPUT`, `EXTRACTION_FAILED`. Each includes `retryable` hint.

**The conversion contract is deliberately two-tier** (mise-ceroru — don't "fix" the second tier into the first):

- **API-facing adapters convert at the adapter layer.** `drive.py` and `gmail.py` (plus thinner docs/sheets/slides call sites) map Google's HTTP taxonomy to `MiseError` kinds — the status code carries meaning (403→PERMISSION_DENIED, 404→NOT_FOUND, 429→RATE_LIMITED) and a `retryable` hint the tools layer couldn't reconstruct from a bare exception.
- **Processing adapters raise bare.** The conversion family (office, pdf, conversion, image, charts, forms, activity, calendar, genai, cdp) raises `ValueError`/`Exception` or returns None: their failures are local processing errors whose message IS the diagnostic — a MiseError wrapper would add ceremony, not information.
- **Every tools-layer funnel converts uniformly, so nothing reaches an MCP response raw:** fetch (`tools/fetch/router.py` — MiseError→its kind, ValueError→invalid_input, Exception→unknown), search (`tools/search.py` — per-source catch into `errors[]`; one source failing doesn't block the others), do() (`run_operation` in `tools/dispatch.py` — never raises; an exception escaping a handler becomes kind INTERNAL, since handlers format their own errors), and server.py's wrapper as backstop.

New adapter rule: interprets Google API responses directly → convert with kinds; post-processes content → raise with a clear message and let the funnel catch.

## Warnings Pattern

Data models have `warnings: list[str]` fields. Extractors populate them during processing (mutation, not return tuple — preserves simple `str` return type). Exception: `extract_message_content()` returns `tuple[str, list[str]]` for per-message processing.

## File Deposit Structure

```
.mise/
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
| **`search` query is `""` not `None` when omitted** | `query` defaults to `""`. Empty string and absent query are indistinguishable inside `do_search` — both skip the `fullText` clause. If you add a source that needs to distinguish "no query given" from "empty query", use a sentinel (e.g. `query: str \| None = None` and check `is None`). Don't assume `""` means "give me everything" — the type/folder_id validation gate catches the all-empty case. |
| **Image embedding needs public sharing** | `do(create)` with local image refs uses Docs API `insertInlineImage`, which requires a publicly accessible URL. Images are uploaded to Drive, shared publicly for seconds, then permissions revoked and temp files deleted. Enterprise Workspace accounts with DLP policies may block the `permissions.create` call — images will be skipped with `cues.image_errors`, doc is still created. |
| **`file_path` is stdio-only (gated)** | `file_path` on `do(create)` and `do(overwrite)` reads the server's disk. Remote mode rejects it outright in server.py's do() wrapper (the boundary). In stdio it's deliberately unrestricted — any readable path works, including `/tmp` and `~/scratch` (mise-jebude: the old cwd-containment rail rejected natural staging spots while guarding nothing, since the same Claude can Read any file and pass `content=`). |
| **Checkbox tick-state is export-only** | Google Docs checkbox checked-state is NOT in the Docs API (`documents.get` returns identical bullet dicts for checked/unchecked). `adapters/docs.py::_apply_checkbox_states` fetches the `text/markdown` export as an oracle — a **2nd API call, only when a checkbox list is present** (`is_checkbox_list` gate) — parses `[ ]`/`[x]` in document order, and tags each paragraph. Count-mismatch → plain bullets + a warning cue (never a wrong tick). The `~~` on checked rows in the export is synthesised by Google's renderer from the checked bit, NOT `textStyle.strikethrough` — don't try to read it from the API. |
| **`comments.md` locates comments (docs only)** | On a Doc fetch, `_enrich_with_comments` passes the doc content to the comments extractor, which correlates each comment's anchor against the document tree — comments render in **document order** with a `↳` locator (nearest heading › sub-group), and heading/group-anchored comments are flagged `⚠` (they scope the whole section). Sheets/slides pass no content and keep the flat API-order render. Anchor text is HTML-unescaped; multi-line span anchors quote every line. |

## Development

```bash
uv sync --all-extras                                # Install deps (full build + dev tools)
uv run --extra extraction python server.py          # Run MCP server (stdio, full build)
uv run --extra extraction python server.py --remote # StreamableHTTP on :8000/mcp
uv run python server.py --help                      # CLI help
uv run --all-extras python -m pytest                # Run tests (suite ASSUMES the full build)
uv run --all-extras python -m pytest tests/unit     # Unit tests only (fast, mocked)
uv run --all-extras python -m mypy models.py extractors/ adapters/ validation.py workspace/
```

Integration tests require `-m integration` flag and real credentials.

### Build flavours (mise-hibere, 0.7.9)

Local extraction (`markitdown[pdf]`, `pdf2image`) lives in an optional `extraction`
extra, **not** core. Two flavours result:

- **Full** — dev/CI and the marketplace plugin (the plugin spawns `uv run --extra
  extraction`). Fast local PDF text, HTML→markdown, PDF page thumbnails.
- **Slim / embedded** — what Cornichon vendors (`vendor.sh` installs plain core, no
  extra). `markitdown` is absent, so `adapters/pdf.py` degrades to **Drive
  server-side conversion** for PDF text, `html_convert.py` to tag-stripping, and PDF
  thumbnails are skipped. Image fetch still works (`pillow` is core).

Two things follow: (1) **run the test suite with `--extra extraction`** (or
`--all-extras`) — PDF-extraction tests assume markitdown is present and fail in a
slim env; (2) the slim PDF→Drive fallback needs Drive **write** scope (it uploads to
convert) — fine for Cornichon, whose PDFs come from the user's own Drive.

### Call Log

Every MCP tool call is logged to `~/.local/share/mise/calls.jsonl` (5 MB rotation, 3 backups). Fields: `ts`, `tool`, `params`, `ok`, `error` (on failure), `result` (key summary fields). Useful for debugging ghost docs, bad params, or unexpected tool behaviour without adding print statements.

```bash
# Last 10 calls
tail -10 ~/.local/share/mise/calls.jsonl | python3 -c "import json,sys; [print(json.dumps(json.loads(l), indent=2)) for l in sys.stdin]"

# Failed calls only
grep '"ok": false' ~/.local/share/mise/calls.jsonl | tail -5
```

## OAuth

**In-app bootstrap (canonical):** `mise.do(operation="setup_oauth")` — opens a Mac browser at the consent screen, runs a detached subprocess listener on `localhost:3000`, saves the token to macOS Keychain via `save_token`. Returns immediately with the URL inline as a fallback. This is the path Cowork users hit; it's also the path the friendly error wrapper in `adapters/http_client.py` points at when the token is missing.

**CLI fallback:**
```bash
uv run python -m auth --auto              # Auto (opens browser, runs listener, saves token)
uv run python -m auth                     # Headless — prints URL, paste back via --code
uv run python -m auth --code URL_OR_CODE  # Exchange code from headless flow
```

`credentials.json` (OAuth client config, not secret) ships with the repo. The OAuth client lives in ITV's `mit-workspace-mcp-server` GCP project with **User type: Internal** — any `@itv.com` Workspace account can authenticate without verification or a test-user list. Token auto-refreshes; `clear_service_cache` handles revoked refresh tokens. Maintainer can also fetch credentials from GCP Secret Manager as fallback.

Token storage: macOS Keychain (`mise-oauth-token`) is the source of truth. `~/.claude/plugins/data/mise-batterie-de-savoir/token.json` is the persistent fallback (auto-created since 2026-05). The plugin-staging-dir token path is ephemeral on Cowork and should never be relied on.

## How to Add a New Content Type

1. **Adapter** — Create `adapters/{type}.py` with fetch function (API calls, returns data)
2. **Extractor** — Create `extractors/{type}.py` with pure extraction function (data in, markdown out)
3. **Wire in tools** — Add handler in `tools/fetch/` and route in `tools/fetch/router.py`
4. **Model** — Add data model in `models.py` if needed
5. **Fixture** — Add to `fixtures/{type}/`, capture via `scripts/capture_fixtures.py`
6. **Tests** — Unit test for extractor (fixture → expected output), adapter mock test

## How to Add a New do() Operation

1. **Implementation** — Create `tools/{op}.py` with `do_{op}()` that validates its own params (accepts `str | None`) and returns `DoResult` on success or error dict on failure
2. **Dispatch** — Add handler to `DISPATCH` dict and required params to `REQUIRED_PARAMS` in `tools/dispatch.py`
3. **Register** — Add name to `OPERATIONS` in `tools/__init__.py`
4. **Export** — Add `do_{op}` to `tools/__init__.py` imports and `__all__`
5. **Resource docs** — Update `docs_do()` in `resources/docs.py` with new operation
6. **Tests** — Unit test for the implementation + `test_dispatch.py` verifies OPERATIONS/DISPATCH sync automatically

## Field Reports

`docs/` contains field reports capturing real-world skill/tool gaps. Pattern: notice gap → write field report → fix → commit together.
