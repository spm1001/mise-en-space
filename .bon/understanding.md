# Understanding — mise-en-space

Mise-en-space is a Google Workspace MCP server that gives Claude access to Drive and Gmail through three verbs: search, fetch, and do. The architecture follows a strict layering — extractors (pure functions), adapters (thin API wrappers), tools (MCP wiring) — and this separation is load-bearing, not cosmetic.

## Remote mode architecture

The remote mode (StreamableHTTP for Claude.ai connectors) was designed around the principle of *not touching the fetchers*. The 10+ type-specific fetch functions in `tools/fetch/` are battle-tested and complex. Rather than making them remote-aware, `server.py` intercepts after the fetch completes: deposits go to a temp dir, content is read back and included inline in the response, then the temp dir is cleaned up. This "post-hoc read-back" pattern means all fetchers work unchanged in both modes. The same applies to search — `SearchResult.to_dict()` already had an inline mode (`path=None`), so remote just clears the path after deposit. When extending for new content types, keep the interception at `server.py`, never inside the fetchers themselves.

Operation gating uses a whitelist (`_REMOTE_ALLOWED_OPS`), not a blacklist. The six allowed ops (create, draft, reply_draft, archive, star, label) were audited Mar 2026. Error messages list only allowed ops, never restricted ones — no leakage.

The tool description adapts per mode via a conditional `description=` on `@mcp.tool()`, which requires `_REMOTE_MODE` to be set at module load time before decorators run. For containers, use `MISE_REMOTE=1` env var — `sys.argv` detection is fragile under process managers.

## Remote deployment path

Explicitly **single-user** (one `token.json`, one `lru_cache(maxsize=1)` per service). Multi-tenancy would require per-request credential injection — architecturally significant, confirmed design choice.

**Tailscale Funnel** is the tunnel (100 conn/min rate limit, 1MB/s throughput cap). Remaining remote path: auth middleware (mise-tokiju) → token management (mise-winala) → containerisation + deploy (mise-sefepo).

Token refresh for long-running server requires: refresh the token file AND call `clear_service_cache()` AND rebuild services. The `lru_cache` bakes in a `Credentials` object at service creation time.

## OAuth and credential architecture

OAuth client is installed-app type — client secret is intentionally distributable. `credentials.json` ships with the repo. The real secret is the refresh token in the plugin data dir.

GCP project: `planetmodha-tools` (personal). Secret: `aby-hemimi-credentials`.

**Two-account architecture:** `claude@planetmodha.com` (daemon/aboyeur) and `sameer.modha@itv.com` (stdio/plugin). They share a codebase but need separate `token.json` paths. Until mise-jatadu is done, only one can be active at a time.

Token storage: `~/.claude/plugins/data/mise-batterie-de-savoir/` (version-stable) with auto-migration from versioned cache dirs.

## Google Sheets: the merged-cell trap

Google's Sheets value APIs (`values.batchGet`) return *display* data, not *structural* data. Merge ranges live in the metadata endpoint (`spreadsheets.get`), not the value endpoint. When you fetch values with `FORMATTED_VALUE`, merged cells return the value only in the top-left cell; every other cell in the merge range comes back as an empty string. This is invisible at the CSV layer — the data looks clean, just wrong.

The ITV regionality session lost hours to 94 false `no_service` results that were actually substitutions hidden by merged cells. The fix was straightforward once diagnosed: request `sheets.merges` in the metadata fields, `_resolve_merges` propagates top-left values post-fetch. But the *discovery* required a human screenshot — the data was plausible enough that Claude confidently presented it as correct.

**Lesson:** Be sceptical of "clean" tabular data from Sheets. If a cue says `merged_cell_count > 0`, pay attention to the merged regions. `_resolve_merges` propagates top-left values into ALL cells of a merge range, including horizontal merges — for merged column headers (e.g. "Q1 2026" spanning 3 months), each column gets the same value. Strictly better than empty strings but could mislead about tabular structure. The warning cue mitigates.

## httpx migration — complete (Mar 2026)

All adapters and tools use sync httpx. Phase 2 (async) is future work: convert tools layer and server.py to `async def`, switch to async client, restructure for real concurrency with `asyncio.gather()`.

Key patterns: `MiseSyncClient` and `MiseHttpClient` are intentional near-duplicates (MiseSyncClient dies in Phase 2). Google API URLs are hardcoded constants. `services.py` is dead code kept only for integration test scaffolding.

## Image embedding architecture

`do(create)` with `doc_type='doc'` supports `![alt](local/path.png)` via post-creation Docs API `batchUpdate`. Critical constraint: Docs API `insertInlineImage` requires a publicly accessible URL — no "insert from Drive file ID" equivalent. Enterprise DLP policies will 403 on the sharing step. GCS signed URLs is the clean alternative (tracked by mise-hagaru).

## Code patterns

- **Folder cues** in `tools/create.py` use a shared `_resolve_folder_cues()` helper
- **PDF rendering** is platform-adaptive: CoreGraphics on macOS, pdf2image/poppler on Linux
- `get_deposit_folder` wipes on re-call — never call twice for the same folder mid-operation (retry hazard in remote mode)
- `MiseSyncClient.upload_multipart()` boundary is UUID-based (binary uploads could contain static boundaries)
- google-auth `Credentials.valid` only checks local `expiry` — httpx client retries once on 401 with forced refresh

## Current state (Apr 2026)

Core MCP server stable and in daily use via stdio. Remote mode transport done. Web fetching fully removed — Workspace-only. Apps Script email extractor ported from archived repo. Merged-cell resolution shipped in 0.5.7 with 10 new tests (1525 total passing).

Backlog: remote deployment path (auth → tokens → container), image/PDF edge cases, Docs heading numbering bug (mise-gonase), quality-of-life features under mise-vakabu, Gmail pagination (mise-putadu).
