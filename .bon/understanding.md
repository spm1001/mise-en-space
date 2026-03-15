# Understanding — mise-en-space

Mise-en-space is a Google Workspace MCP server that gives Claude access to Drive and Gmail through three verbs: search, fetch, and do. The architecture follows a strict layering — extractors (pure functions), adapters (thin API wrappers), tools (MCP wiring) — and this separation is load-bearing, not cosmetic.

## Remote mode architecture

The remote mode (StreamableHTTP for Claude.ai connectors) was designed around the principle of *not touching the fetchers*. The 10+ type-specific fetch functions in `tools/fetch/` are battle-tested and complex. Rather than making them remote-aware, `server.py` intercepts after the fetch completes: deposits go to a temp dir, content is read back and included inline in the response, then the temp dir is cleaned up. This "post-hoc read-back" pattern means all fetchers work unchanged in both modes. The same applies to search — `SearchResult.to_dict()` already had an inline mode (`path=None`), so remote just clears the path after deposit. When extending for new content types, keep the interception at `server.py`, never inside the fetchers themselves.

Operation gating for remote mode uses a whitelist (`_REMOTE_ALLOWED_OPS`), not a blacklist. Error messages list only allowed ops, never the restricted ones — preventing leakage of unexposed operations. The tool description adapts per mode via a conditional `description=` on `@mcp.tool()`, which requires `_REMOTE_MODE` to be set at module load time before decorators run. This early-evaluation pattern is intentional — don't refactor it without understanding the timing constraint. For containers, use `MISE_REMOTE=1` env var exclusively — `sys.argv` detection is fragile under process managers.

Temp dir allocation in `_fetch_remote` and `_search_remote` is conditional: only created when no `base_path` is provided. This avoids unnecessary filesystem churn on a concurrent server. The `get_deposit_folder` wipe-on-call pattern (documented in CLAUDE.md) creates a retry hazard in remote mode — HTTP client retries can trigger double-wipe. Don't add automatic retry at the HTTP level for fetch operations.

## Remote deployment path — decisions and sequencing

The remote push is explicitly **single-user** (one `token.json`, one `lru_cache` per service). The `lru_cache(maxsize=1)` on service getters in `adapters/services.py` is fundamentally single-tenant — multi-tenancy would require per-request credential injection, which is architecturally significant. This is a confirmed design choice, not a gap.

**Tailscale Funnel** is the tunnel, not Cloudflare Tunnel — fewer moving parts since tailscaled is already on kube. Funnel has a 100 conn/min rate limit and 1MB/s throughput cap that should be tested with representative payloads (large inline doc responses).

**Sequencing constraint:** the httpx migration (mise-fokoli) must complete before containerisation (mise-sefepo). The sync `googleapiclient` library blocks the event loop under concurrent load via `asyncio.to_thread()`, hitting Python's default `ThreadPoolExecutor` limit. For a long-running server this is a scalability ceiling, not just a nice-to-have refactor. mise-sefepo is formally blocked on this.

**Token refresh for long-running server** requires a specific sequence: refresh the token file AND call `clear_service_cache()` AND rebuild services. The `lru_cache` bakes in a `Credentials` object at service creation time — refreshing `token.json` on disk without clearing the cache does nothing.

## Current state (Mar 2026)

Web fetching code has been fully removed — mise is Workspace-only. The core MCP server is stable and in daily use via stdio. Remote mode transport and content delivery are done (StreamableHTTP, inline content, safe-op filter). The remaining remote path is: auth middleware → token management → httpx migration → containerisation + deploy.

The backlog includes edge-case polish (image/PDF, GIF handling), a latency/observability initiative (profiling, telemetry), and several feature additions (Apps Script port, meeting prep, calendar write ops, image embedding in Docs). These are all Tier 3 — valuable but not blocking the remote deployment.
