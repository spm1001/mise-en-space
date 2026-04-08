# Understanding — mise-en-space

Mise-en-space is a Google Workspace MCP server that gives Claude access to Drive and Gmail through three verbs: search, fetch, and do. The architecture follows a strict layering — extractors (pure functions), adapters (thin API wrappers), tools (MCP wiring) — and this separation is load-bearing, not cosmetic.

## The generic primitive beats the convenience alias

When mise added `archive`, `star`, and `label` as do() operations, `label` was already the superset — archive is `label("INBOX", remove=True)`, star is `label("STARRED")`. A brief proposed adding `mark_read`, `mark_unread`, and `unstar` as three more convenience aliases. But each new operation name costs tokens in three places: tool description (hard-capped at 2048ch), dispatch table, and Claude's reasoning about which op to pick. The right move was zero new operations — just better docs showing that `label` with system label names covers all triage actions.

**Before adding a new do() operation, check whether an existing operation already handles it with different parameters.** The description ceiling makes this more than aesthetics — it's a resource constraint.

## The MCP description length ceiling

MCP tool descriptions have a hard ceiling at 2048 characters, enforced by Claude Code's `MAX_MCP_DESCRIPTION_LENGTH` constant at `src/services/mcp/client.ts:218`. Descriptions over this limit are sliced and suffixed with `'… [truncated]'`. This truncation triggers a secondary, more insidious failure: the Anthropic API silently drops properties from the tool's JSON schema during `tool_reference` expansion (the mechanism that expands deferred tools when ToolSearch is called). The schema is sent in full by CC — the property loss happens API-side during expansion, not during transmission.

**Diagnostic signature:** ToolSearch returns fewer properties than `mcp._tool_manager._tools[name].parameters` reports. The dropped property appears nowhere in the ToolSearch result — not truncated, just absent. When Claude emits a tool_use including the missing property, the API strips it before CC sees the tool_use block, so CC's `z.object({}).passthrough()` never gets the chance to help.

**Fix within MCP server control:** Keep tool descriptions short and put detail in MCP resources (`mise://tools/*`). The `do()` description went from a full Args/Returns docstring (2494ch) to a compact operation summary (~600ch). All 21 properties survived immediately. Don't expand descriptions without checking `len(tool.description) + len(json.dumps(tool.parameters))` against practical limits. This applies to all batterie MCP servers, not just mise.

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

Google's Sheets value APIs (`values.batchGet`) return *display* data, not *structural* data. Merge ranges live in the metadata endpoint (`spreadsheets.get`), not the value endpoint. When you fetch values with `FORMATTED_VALUE`, merged cells return the value only in the top-left cell; every other cell in the merge range comes back as an empty string.

The fix: request `sheets.merges` in the metadata fields, `_resolve_merges` propagates top-left values post-fetch. `_resolve_merges` handles horizontal merges too — merged column headers get the same value in each spanned column. The warning cue mitigates misinterpretation.

## httpx migration — complete (Mar 2026)

All adapters and tools use sync httpx. Phase 2 (async) is future work. Key patterns: `MiseSyncClient` and `MiseHttpClient` are intentional near-duplicates (MiseSyncClient dies in Phase 2). Google API URLs are hardcoded constants. `services.py` is dead code kept only for integration test scaffolding.

## Image embedding architecture

`do(create)` with `doc_type='doc'` supports `![alt](local/path.png)` via post-creation Docs API `batchUpdate`. Critical constraint: Docs API `insertInlineImage` requires a publicly accessible URL — no "insert from Drive file ID" equivalent. Enterprise DLP policies will 403 on the sharing step. GCS signed URLs is the clean alternative (tracked by mise-hagaru).

## Gmail ecosystem landscape

Three Workspace MCP servers were evaluated (repos cloned to `~/Repos/third-party/`): taylorwilsdon/google_workspace_mcp (2k stars), GongRzhe/Gmail-MCP-Server (1k stars), aaronsb/google-workspace-mcp (143 stars).

**Safety model spectrum:** GongRzhe has no safety — permanent batch deletion without confirmation. taylorwilsdon's hardened fork removes dangerous code entirely. aaronsb has the gold standard: a composable policy engine (`src/factory/safety.ts`) with `allow`/`block`/`downgrade` actions — "downgrade" silently converts send to draft, reducing blast radius without blocking the agent. Mise's `_REMOTE_ALLOWED_OPS` is spiritually similar but coarser (binary allow/block, no downgrade). If mise ever needs finer-grained safety, aaronsb's pattern is the reference.

**Batch + pagination is the infrastructure pair that makes triage viable.** Neither alone is sufficient — batch without pagination means acting fast on a partial picture; pagination without batch means seeing everything but acting painfully slowly. The combination (80 threads fetched, 18 archived in 3 tool calls in live testing) crosses from "demo" to "useful." The sequential-per-thread implementation is correct — Gmail's `messages.batchModify` works at message level not thread level, so using it would require resolving thread→message IDs first (one extra API call per thread), netting zero gain for typical triage volumes.

**Content extraction is where mise leads.** Signature stripping (talon), eager attachment extraction, markitdown HTML conversion, Drive pre-exfil optimisation — mise's fetch quality is ahead of all three competitors. The gap is in breadth of Gmail actions and discoverability of search syntax, not content processing.

## Code patterns

- **Folder cues** in `tools/create.py` use a shared `_resolve_folder_cues()` helper
- **PDF rendering** is platform-adaptive: CoreGraphics on macOS, pdf2image/poppler on Linux
- `get_deposit_folder` wipes on re-call — never call twice for the same folder mid-operation (retry hazard in remote mode)
- `MiseSyncClient.upload_multipart()` boundary is UUID-based (binary uploads could contain static boundaries)
- google-auth `Credentials.valid` only checks local `expiry` — httpx client retries once on 401 with forced refresh
- **Token error diagnostics** — `_load_and_diagnose_credentials` in `adapters/http_client.py` distinguishes missing file, corrupt JSON, no refresh_token, and failed refresh. Clear error messages for each case.
- **Heading extraction** — blockquote prefix suppressed when a heading prefix is already present (`extractors/docs.py`), preventing `> ##` output for indented headings.

## Current state (Apr 2026)

Core MCP server stable and in daily use via stdio (v0.5.12). Remote mode transport done. Merged-cell resolution, pageless doc creation, folder creation, token diagnostics, and heading extraction all shipped. MCP description length fixed (property drop bug resolved). Apps Script email extractor ported from archived repo.

Gmail capabilities: search operators exposed as MCP resource, label IDs in data model, live labels directory resource (`mise://gmail/labels`), `list_labels()` in gmail adapter. Write operations (draft, reply_draft, archive, star, label) shipped Feb 2026. Triage docs updated to show `label` covers mark_read/unread/unstar — no separate ops needed (see "generic primitive" principle above). Batch ops (archive/star/label accept `file_id` as `str | list[str]`) and search pagination (follows `nextPageToken`, surfaces `truncated` flag as cue warning) both shipped Apr 2026. Next: workspace skill update (mise-dobida), then is_unread investigation (mise-daduti).

**Known bug: `is_unread` always false** — filed as mise-daduti. Every search result shows `is_unread: false` despite unread threads visible in UI. Could be fields mask issue or threads.get behaviour. Don't rely on unread status for triage prioritisation until investigated.

Backlog: remote deployment path (auth → tokens → container), image/PDF edge cases (mise-heferu), Drive shortcuts (mise-nitaco), keychain token materialisation (mise-zozewa), image embedding privacy (mise-hagaru), calendar forward-looking (mise-milizo).
