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
- **Shadow field masks** — When an adapter defines a constant for API field selection but the call site uses a locally-defined string, mocked tests pass because mocks return whatever you tell them regardless of which fields were requested. The `is_unread` bug was this: `SEARCH_THREAD_FIELDS` had `labelIds` but `search_threads()` used a local `search_fields` that didn't. Guard by testing the mask itself (`assert "labelIds" in fields`), not just downstream logic. Any time you see a fields/projection/select mask in an API adapter, verify the call site actually uses the constant.

## Observability and stock-taking

The call log (`~/.local/share/mise/calls.jsonl`) is the primary operational data source. A stock-taking session (Apr 2026, ~375 calls over 19 days) revealed patterns invisible from feature work alone: fetch 47%, search 33%, do 20%. Gmail edges out Drive as most-searched source. `replace_text` is the top do() op. Six ops have zero real-world usage (draft, rename, share, prepend, star, label). Activity and calendar search sources are dormant. 99% success rate. The lesson: **periodically step back from building to observe how the thing is actually used** — the observation often matters more than the next feature.

Code health (same session): 92% test coverage, zero layer violations, dispatch table sync verified, tool descriptions within 2048 limit, no TODO/FIXME/HACK. Mypy errors 30→22 (remaining are upstream httpx/orjson noise — the right fix is a thin `_parse_json` wrapper centralising one ignore, not scattershot `type: ignore`).

## Current state (Apr 2026)

Core MCP server stable and in daily use via stdio (v0.5.13). Remote mode transport done. Merged-cell resolution, pageless doc creation, folder creation, token diagnostics, and heading extraction all shipped. MCP description length fixed (property drop bug resolved). Apps Script email extractor ported from archived repo.

Gmail capabilities: search operators exposed as MCP resource, label IDs in data model, live labels directory resource (`mise://gmail/labels`), `list_labels()` in gmail adapter. Write operations (draft, reply_draft, archive, star, label) shipped Feb 2026. Triage docs updated to show `label` covers mark_read/unread/unstar — no separate ops needed (see "generic primitive" principle above). Batch ops (archive/star/label accept `file_id` as `str | list[str]`) and search pagination (follows `nextPageToken`, surfaces `truncated` flag as cue warning) both shipped Apr 2026. Workspace skill updated with full Gmail coverage (dobida). `is_unread` bug fixed — search fields mask was missing `labelIds` (daduti). mise-wiboka outcome complete.

## Claude Desktop integration (Apr 2026, mise-hohoku)

**Mise works in all Desktop modes (Chat, Cowork, Code) via two paths:**

1. **`claude_desktop_config.json` → `mcpServers`** — Desktop runs the stdio server on the Mac and bridges it as a "connector" into Cowork's VM. The VM never connects directly; Desktop proxies. This is the simplest path for personal use.

2. **MCPB extension (`.mcpb` file)** — the packaging format for distributing MCP servers. `manifest.json` with `"server.type": "uv"` lets Desktop auto-install Python + deps. Zero CLI for end users. Validated and tested: `mise-en-space-0.5.13.mcpb` (355KB). New extension install requires a full Desktop restart (not just new session).

**What doesn't work in Cowork (yet):**
- **Uploaded plugin MCP servers** — Desktop reads `.mcp.json`, shows the connector in UI, registers permissions, but `LocalPluginsReader` returns 0 and the server never starts. The plugin spec (`cowork-plugin-management/create-cowork-plugin`) documents this as supported. 90% of the plumbing is there. Likely a "not yet" or a missing toggle — don't accept "can't work" without re-investigating.
- HTTP MCP from `.mcp.json` pointing at local IPs — gvisor networking blocks host access (`172.16.10.1` unreachable), `allowedDomains=1` restricts egress. Connectors route through Anthropic's cloud, not local network.
- The Cowork VM is Linux ARM64, Python 3.10, ephemeral, with a MITM proxy for all outbound.

**Three separate extension systems in Desktop (they don't share plumbing):**
1. **MCPB extensions** (`Claude Extensions/`) → `LocalMcpServerManager` → MCP tools work, skills ignored
2. **Uploaded plugins** (`rpm/`) → `RemotePluginManager` → skills work, MCP servers not launched
3. **`claude_desktop_config.json`** → `mcpServers` → MCP tools work, no skills mechanism

**MCPB packaging:** `manifest.json` + `pyproject.toml` + source code. `"server.type": "uv"` makes Desktop auto-install Python + deps. `mcpb validate` and `mcpb pack` via `@anthropic-ai/mcpb` CLI. `.mcpbignore` excludes dev artifacts. `${__dirname}` for portable paths. Resources listed but not bridged into Cowork (tools are bridged).

**Full-fat plugin built:** `/tmp/mise-full-plugin.zip` (308KB, 74 files) — bundled source, `${CLAUDE_PLUGIN_ROOT}` paths, `.mcp.json` at root, workspace skill. Ready to become the one-zip solution when the MCP launch gap is fixed.

**Cowork plugin architecture:** `cowork-plugin-shim.sh` (in app bundle) reveals compiled-binary plugins with `cowork_require_token` for credential injection and `cowork_gate` for permission bridging via filesystem IPC. First-party Gmail/Calendar MCPs (`gmail.mcp.claude.com`) are Anthropic-hosted HTTP services.

**Strategic outcome:** Remote deployment path (tokiju → winala → sefepo) parked as someday/maybe. Only needed for Claude.ai web without Desktop, or mobile.

Backlog: image/PDF edge cases (mise-heferu), Drive shortcuts (mise-nitaco), keychain token materialisation (mise-zozewa), image embedding privacy (mise-hagaru), calendar forward-looking (mise-milizo), plugin MCP launch gap investigation.
