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

**Sequencing constraint:** the httpx migration (mise-fokoli) must complete before containerisation (mise-sefepo). The sync `googleapiclient` library blocks the event loop under concurrent load via `asyncio.to_thread()`, hitting Python's default `ThreadPoolExecutor` limit. For a long-running server this is a scalability ceiling, not just a nice-to-have refactor. mise-sefepo is formally `bon wait`-blocked on this.

**Token refresh for long-running server** requires a specific sequence: refresh the token file AND call `clear_service_cache()` AND rebuild services. The `lru_cache` bakes in a `Credentials` object at service creation time — refreshing `token.json` on disk without clearing the cache does nothing.

## Overwrite markdown rendering gap

Create renders markdown (bold, tables, lists) because it uses Drive's import engine (`files().create()` with `mimetype="text/markdown"`). Overwrite uses Docs API `insertText` which has no conversion — markdown syntax appears literally. The likely fix is using `files().update()` with the same markdown MIME type, but this needs verification: `scripts/test_overwrite_conversion.py` tests whether `files().update()` triggers the same import conversion as `files().create()`. Run it with real credentials to unblock mise-zobuci. If update conversion doesn't work, fall back to temp-doc-body-copy (documented in the brief).

## Code patterns worth knowing

**Folder cues** in `tools/create.py` use a shared `_resolve_folder_cues()` helper. All three create functions (_create_doc, _create_sheet, _create_file) call it. Previously copy-pasted three times.

**PDF rendering** is platform-adaptive: CoreGraphics on macOS (5.7ms/page), pdf2image/poppler on Linux (83ms/page). The remote host is Debian — `poppler-utils` is a hard requirement in the Dockerfile. Text extraction works without it; thumbnails don't.

## httpx migration — in progress (Mar 2026)

The httpx migration (mise-fokoli) is **two-phase by design** (see `docs/decisions.md`):

**Phase 1 (in progress):** Replace `googleapiclient` with `httpx.Client` (sync) one adapter at a time. Adapters use `get_sync_client()` from `adapters/http_client.py`. Callers don't change. Tests mock `get_sync_client` returning a MagicMock with `.get_json()`, `.get_bytes()`, `.post_json()`, etc. The `retry.py` `_get_http_status()` was extended to extract status from `httpx.HTTPStatusError.response.status_code`.

**Completed:** docs.py, drive.py (12 functions including streaming downloads and comments pagination). **Remaining:** sheets, slides, calendar, activity, gmail, then delete `services.py` and `googleapiclient` deps.

**Phase 2 (future, single-shot):** When all adapters use httpx, convert the tools layer and server.py to `async def`. Switch `get_sync_client()` → `get_http_client()` (async), add `await`, delete `MiseSyncClient`. Then restructure for real concurrency — `asyncio.gather()` for metadata+comments, search sources, slides thumbnails.

**Key things to know about the migration:**
- `MiseSyncClient` and `MiseHttpClient` in `adapters/http_client.py` are intentional near-duplicates. Don't consolidate them — MiseSyncClient dies in Phase 2.
- Google API URLs are hardcoded constants (e.g., `_DRIVE_API = "https://www.googleapis.com/drive/v3/files"`), not discovered at runtime. Unit tests mock the client so **wrong URLs won't be caught** — verify against Google REST docs or run integration tests.
- `upload_file_content` uses `client.request()` + `orjson.loads()` manually because the upload uses a file MIME type but returns JSON. Slight wart.
- `download_file_to_temp` replaced `MediaIoBaseDownload` (resumable) with plain HTTP streaming (`stream_to_file`). If interrupted, the retry decorator restarts from scratch rather than resuming.
- Tools that import `get_drive_service` directly (create.py, move.py, rename.py, share.py) bypass the adapter layer — they need separate migration.
- During the transition, two credential objects coexist (one in `adapters.services`, one in `adapters.http_client`). Each refreshes independently. Harmless but worth knowing.
- No benchmarks were run during Phase 1. The brief said "benchmark each" but the profiling infrastructure (mise-cadadi) doesn't exist yet. orjson + connection pooling should be faster but we have no data.

## Overwrite — resolved (Mar 2026)

The overwrite markdown rendering gap (mise-numado) is fixed. `files().update()` with `text/markdown` media type triggers Drive's import engine — same conversion as `files().create()`. All markdown formatting (headings, bold, tables, lists) renders automatically. The old Docs API path (delete → insertText → apply heading styles, UTF-16 position tracking) was deleted entirely.

## Current state (Mar 2026)

Web fetching code has been fully removed — mise is Workspace-only. The core MCP server is stable and in daily use via stdio. Remote mode transport and content delivery are done (StreamableHTTP, inline content, safe-op filter). The remaining remote path is: auth middleware (mise-tokiju) → token management (mise-winala) → httpx migration (mise-fokoli, in progress) → containerisation + deploy (mise-sefepo).

The httpx migration is in progress — docs and drive adapters done, six more to go. Pattern is established and mechanical. See section above for details.

The backlog includes edge-case polish (image/PDF, GIF handling), a latency/observability initiative (profiling, telemetry), and several feature additions (Apps Script port, meeting prep, calendar write ops, image embedding in Docs, aboyeur Gmail polling). These are all Tier 3 — valuable but not blocking the remote deployment.
