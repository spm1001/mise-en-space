# Understanding — mise-en-space

Mise-en-space is a Google Workspace MCP server that gives Claude access to Drive and Gmail through three verbs: search, fetch, and do. The architecture follows a strict layering — extractors (pure functions), adapters (thin API wrappers), tools (MCP wiring) — and this separation is load-bearing, not cosmetic.

## Remote mode architecture

The remote mode (StreamableHTTP for Claude.ai connectors) was designed around the principle of *not touching the fetchers*. The 10+ type-specific fetch functions in `tools/fetch/` are battle-tested and complex. Rather than making them remote-aware, `server.py` intercepts after the fetch completes: deposits go to a temp dir, content is read back and included inline in the response, then the temp dir is cleaned up. This "post-hoc read-back" pattern means all fetchers work unchanged in both modes. The same applies to search — `SearchResult.to_dict()` already had an inline mode (`path=None`), so remote just clears the path after deposit. When extending for new content types, keep the interception at `server.py`, never inside the fetchers themselves.

Operation gating uses a whitelist (`_REMOTE_ALLOWED_OPS`), not a blacklist. The six allowed ops (create, draft, reply_draft, archive, star, label) were audited Mar 2026: create is the broadest — `doc_type='file'` can write arbitrary content to any accessible folder. Acceptable for single-user; reconsider if ever multi-tenant. draft/reply_draft are safe (drafts not sent). archive/star/label are metadata-only. Excluded ops: move, rename, share (exposes files), overwrite/prepend/append/replace_text (destructive). Error messages list only allowed ops, never restricted ones — no leakage. The audit is documented in server.py comments near `_REMOTE_ALLOWED_OPS`.

The tool description adapts per mode via a conditional `description=` on `@mcp.tool()`, which requires `_REMOTE_MODE` to be set at module load time before decorators run. This early-evaluation pattern is intentional — don't refactor it without understanding the timing constraint. For containers, use `MISE_REMOTE=1` env var exclusively — `sys.argv` detection is fragile under process managers.

Temp dir allocation in `_fetch_remote` and `_search_remote` is conditional: only created when no `base_path` is provided. This avoids unnecessary filesystem churn on a concurrent server. The `get_deposit_folder` wipe-on-call pattern (documented in CLAUDE.md) creates a retry hazard in remote mode — HTTP client retries can trigger double-wipe. Don't add automatic retry at the HTTP level for fetch operations.

## Remote deployment path — decisions and sequencing

The remote push is explicitly **single-user** (one `token.json`, one `lru_cache` per service). The `lru_cache(maxsize=1)` on service getters in `adapters/services.py` is fundamentally single-tenant — multi-tenancy would require per-request credential injection, which is architecturally significant. This is a confirmed design choice, not a gap.

**Tailscale Funnel** is the tunnel, not Cloudflare Tunnel — fewer moving parts since tailscaled is already on kube. Funnel has a 100 conn/min rate limit and 1MB/s throughput cap that should be tested with representative payloads (large inline doc responses).

**Sequencing constraint:** the httpx migration (mise-fokoli) is complete. Containerisation (mise-sefepo) is unblocked on that front. The remaining remote path is: auth middleware (mise-tokiju) → token management (mise-winala) → containerisation + deploy (mise-sefepo).

**Token refresh for long-running server** requires a specific sequence: refresh the token file AND call `clear_service_cache()` AND rebuild services. The `lru_cache` bakes in a `Credentials` object at service creation time — refreshing `token.json` on disk without clearing the cache does nothing.

## OAuth and credential architecture

The OAuth client is an installed-app type — the client secret is intentionally distributable (Google's design for desktop/CLI apps, where users can always extract it from the binary). The `credentials.json` ships with the repo for this reason. The real secret is the refresh token, which lives in the plugin data dir and is never committed.

As of March 2026, the GCP project for credentials is `planetmodha-tools` (personal), not `mit-workspace-mcp-server` (ITV). The secret is `aby-hemimi-credentials`.

**Two-account architecture:** `claude@planetmodha.com` is the daemon account used by aboyeur for Gmail polling (served/HTTP mode). `sameer.modha@itv.com` is for stdio/plugin (Sameer's personal use). They share a codebase but need separate `token.json` paths. Until mise-jatadu (configurable token path env var) is done, only one can be active at a time.

Token storage uses `~/.claude/plugins/data/mise-batterie-de-savoir/` (version-stable) with auto-migration from versioned cache dirs. The two-phase PKCE auth flow (get_auth_url saves verifier, authenticate --code loads it) is critical for remote/headless use where Claude can't do interactive stdin.

## Code patterns worth knowing

**Folder cues** in `tools/create.py` use a shared `_resolve_folder_cues()` helper. All three create functions (_create_doc, _create_sheet, _create_file) call it. Previously copy-pasted three times.

**PDF rendering** is platform-adaptive: CoreGraphics on macOS (5.7ms/page), pdf2image/poppler on Linux (83ms/page). The remote host is Debian — `poppler-utils` is a hard requirement in the Dockerfile. Text extraction works without it; thumbnails don't.

## httpx migration — complete (Mar 2026)

The httpx migration (mise-fokoli) is **complete** — all adapters and tools use sync httpx. The migration was two-phase by design (see `docs/decisions.md`):

**Phase 1 (done):** All adapters and tools migrated from `googleapiclient` to `httpx.Client` (sync). Adapters use `get_sync_client()` from `adapters/http_client.py`. Tests mock `get_sync_client` returning a MagicMock with `.get_json()`, `.get_bytes()`, `.post_json()`, etc. The `retry.py` `_get_http_status()` extracts status from `httpx.HTTPStatusError.response.status_code`.

**Phase 2 (future, single-shot):** Convert tools layer and server.py to `async def`. Switch `get_sync_client()` → `get_http_client()` (async), add `await`, delete `MiseSyncClient`. Then restructure for real concurrency — `asyncio.gather()` for metadata+comments, search sources, slides thumbnails.

**Remaining cleanup:** `services.py` has zero production consumers — only integration test scaffolding still imports it. Can be deleted once test setup/teardown is migrated.

**Key things to know:**
- `MiseSyncClient` and `MiseHttpClient` in `adapters/http_client.py` are intentional near-duplicates. Don't consolidate them — MiseSyncClient dies in Phase 2.
- Google API URLs are hardcoded constants (e.g., `_DRIVE_API = "https://www.googleapis.com/drive/v3/files"`), not discovered at runtime. Unit tests mock the client so wrong URLs won't be caught — verify against Google REST docs or run integration tests.
- `upload_file_content` uses `client.request()` + `orjson.loads()` manually because the upload uses a file MIME type but returns JSON.
- `download_file_to_temp` replaced `MediaIoBaseDownload` (resumable) with plain HTTP streaming (`stream_to_file`). If interrupted, the retry decorator restarts from scratch rather than resuming.
- Gmail `search_threads` replaced Google's batch HTTP API with sequential individual GETs. Slightly slower in Phase 1, but simpler code — in Phase 2 these become `asyncio.gather()`, actually faster than batch.
- `MiseSyncClient.upload_multipart()` handles multipart/related encoding for Drive uploads. Used by both `tools/create.py` and `adapters/conversion.py`. Boundary is UUID-based (`mise_{uuid4}`) — changed from static string because binary file uploads (via `file_path` parameter) could contain the boundary in their content.
- google-auth's `Credentials.valid` only checks local `expiry` — if `expiry` is None, reports valid even when expired server-side. The httpx client retries once on 401 with forced `credentials.refresh()`. Unit tests can never catch this class of bug.

## Image embedding architecture

`do(create)` with `doc_type='doc'` supports `![alt](local/path.png)` in markdown content. The implementation uses a post-creation injection pattern via Docs API `batchUpdate`:

1. Parse image refs from markdown, replace with Unicode sentinel placeholders
2. Create the doc via Drive import (markdown → Google Doc)
3. Upload each image to Drive, share publicly (briefly), get public URL
4. Find placeholders in the doc via Docs API, replace with `insertInlineImage`
5. Revoke public sharing, delete temp Drive uploads

**Critical constraint:** Docs API `insertInlineImage` requires a publicly accessible HTTPS URL — no "insert from Drive file ID" equivalent (unlike Slides API). This means enterprise Workspace accounts with DLP policies will 403 on the `permissions.create(type=anyone)` call. Graceful degradation works (images skipped, reported in `cues.image_errors`), but the entire happy path is mocked — batchUpdate format, `uc?export=view` URI, and permission lifecycle have never hit real APIs (tracked by mise-gozati, now closed but see mise-hagaru for the deeper fix).

GCS signed URLs is the clean alternative if enterprise support is needed — time-limited public URL without Drive sharing semantics.

## Current state (Apr 2026)

Web fetching code has been fully removed — mise is Workspace-only. The core MCP server is stable and in daily use via stdio. Remote mode transport and content delivery are done (StreamableHTTP, inline content, safe-op filter). The remaining remote path is: auth middleware (mise-tokiju) → token management (mise-winala) → containerisation + deploy (mise-sefepo).

The httpx migration (mise-fokoli) is complete. Write operation integration tests verified post-migration (mise-vozapu done). `services.py` is dead code kept only for integration test scaffolding. Folder triage (mise-wimamo) and call logging (mise-gakubo) shipped as part of 0.4.2.

Plugin distribution (mise-fipabo) and OAuth smoothing (mise-lolane) are done. Created files are stamped with `description` and `properties.mise=true` for provenance tracking. Version 0.5.1 shipped with file dates in search/fetch, binary uploads (`file_path` on `do(create)`), and image embedding in Docs.

**0.5.1 shipped features:**
- `createdTime`/`modifiedTime` in search results, previews, and fetch manifests (mise-pudibu)
- `file_path` parameter for binary uploads via `do(create, doc_type='file')` (mise-likaba)
- Image embedding in Google Docs via Docs API batchUpdate (mise-dulajo)

The backlog includes: Apps Script email extractor (mise-tagemu, repeatedly deferred — should be prioritised), edge-case polish (image/PDF, GIF handling), Google Sheets merged cell detection (mise-vakabu, marked URGENT), Google Docs heading numbering bug (mise-gonase), and several feature additions (meeting prep, calendar write ops, aboyeur Gmail polling, folder creation, Drive shortcuts). mise-jatadu (configurable token paths) unblocks two-account coexistence.
