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

OAuth client is installed-app type — client secret is intentionally distributable. `credentials.json` ships with the repo. The real secret is the refresh token.

**Two GCP projects, two OAuth clients:** mise's OAuth client lives in ITV's `mit-workspace-mcp-server` GCP project (the production path users hit). A second client in `planetmodha-tools` (secret `aby-hemimi-credentials`) backs aboyeur/daemon use under `claude@planetmodha.com`. The two-account split (`claude@planetmodha.com` daemon vs `sameer.modha@itv.com` stdio/plugin) shares a codebase but needs separate token paths; until mise-jatadu lands, only one can be active at a time.

**OAuth client User type matters and is invisible from `gcloud`.** The `mit-workspace-mcp-server` consent screen is set to **User type: Internal** — meaning any `@itv.com` Workspace user authenticates without verification or being on a test-user list. That bypasses Google's ~6-week verification process for sensitive scopes. External-test caps at 100 explicitly-added users; External-published shows a scary "Google hasn't verified this app" warning. The setting lives at `console.cloud.google.com/apis/credentials/consent?project=<project_id>` and isn't queryable from the `gcloud` CLI. For any future ITV-internal MCP, default to Internal mode.

**Token storage hierarchy:** macOS Keychain (`mise-oauth-token`) is the source of truth. `~/.claude/plugins/data/mise-batterie-de-savoir/token.json` is the persistent fallback. The plugin-staging-dir token path is ephemeral on Cowork and should never be relied on. `resolve_token_path` materialises Keychain → file on each call, then `save_token` deletes the file again — visible churn under mise-zozewa, cosmetic but real.

**In-app bootstrap is the canonical install path:** `mise.do(operation="setup_oauth")` opens a Mac browser, runs a detached `python -m auth --auto` listener on `localhost:3000`, exchanges the code, saves to Keychain. The friendly error in `adapters/http_client.py` points users at this tool by name when the token is missing — Claude reads the error, finds the remedy verbatim, calls it.

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

## MCP Resources: what they're for

Mise ships eight Resources via FastMCP — six static docs (`mise://docs/*`), one live API call (`mise://gmail/labels` → `Gmail labels.list` rendered as markdown), and one parameterised template (`mise://tools/{tool_name}` for auto-generated tool docs). Empirical behaviour (Apr 2026, tested in a live CC session):

- `ListMcpResourcesTool` returns the seven concrete URIs. **Parameterised templates are invisible to discovery** — `ReadMcpResourceTool` resolves them fine if you construct the URI yourself, but listing won't surface them.
- `ReadMcpResourceTool` returns content **inline** in the tool result. Cold-fetching via Resource therefore dumps the whole payload into context — fine for tiny live state, architecturally wrong for content (a 50-page PDF would torch the window).

**The right division of labour:** Resources for small live introspection (label lists, drafts, quota); the existing `fetch` verb (writing to disk) for content acquisition; native filesystem read for steady-state. The URI scheme is **not** load-bearing — `{type, id}` carries everything URIs were carrying. The web URL (`docs.google.com/document/d/abc`) is the human-facing handle if anyone needs one; nothing requires inventing a new scheme. Lesson worth generalising: when an architectural layer X exists "because of Y," and Y gets cut, audit X — it usually goes too.

`mise://gmail/labels` is a model worth copying for other small live state (drafts list, recent activity, drive quota). All small, ambient, no cold-blob problem.

## Workspace cache vision (mise-cuvusa)

Background: 20 `mise/` folders are scattered across `~/Repos/`, ~250MB total — Drive content gets pulled per-cwd, never deduplicated, never grep-able as a corpus. Pre-Dolt history (mise-6bo, mise-fuSepi, three open cleanup bons that never shipped) shows this conversation has happened multiple times.

Survivor design after pushback: central cache + cwd hardlinks + manifest index. Four sub-actions under **mise-cuvusa**:
- **mise-rocume** — workspace manager writes to central cache (`~/.mise/cache/`), cwd `mise/` becomes hardlinks
- **mise-diwosi** — cache has a discoverable index; fetch checks it before round-tripping
- **mise-kaceta** — fetch input is pimuga-safe (explicit type, no silent misroute — e.g. `fetch('gmail:abc')` actually routing to gmail instead of silently going to Drive and 404-ing)
- **mise-gotace** — cache cleanup in one place (TTL or LRU)

**Sequencing risks:** if rocume ships before diwosi, fetch keeps round-tripping during the gap (decide deliberately). If rocume + diwosi ship before gotace, the cache accumulates without bound — ship gotace concurrently or document the manual cleanup path. Hardlinks need cross-filesystem fallback to symlinks (Taildrive mounts, external drives) — verify `os.link` failure handling actually triggers fallback. Before implementing rocume, verify Drive's web URL is the right human-facing handle to carry in the manifest.

## Current state (May 2026)

Core MCP server stable and in daily use via stdio (v0.5.16). Cowork plugin v0.6.0 validated end-to-end (2026-05-04 field test). Remote mode transport done. Merged-cell resolution, pageless doc creation, folder creation, token diagnostics, and heading extraction all shipped. MCP description length fixed (property drop bug resolved). Apps Script email extractor ported from archived repo.

**Google Forms — read side shipped (v0.5.14).** Adapter calls Forms API v1 (`forms.googleapis.com`), extractor renders all question types (choice, text, scale, date, grid, rating) as markdown, deposits `structure.json` (raw API response) for programmatic use alongside `content.md`. Forms are Drive files (`application/vnd.google-apps.form`) — they appear in search and the existing URL detection handles `docs.google.com/forms/d/...` because the router does MIME-based dispatch, not URL-based. The `forms.body.readonly` scope was added for read. Three MCP resource strings (`docs/fetch`, `docs/overview`, `docs/workspace`) plus understanding.md need updating for any new content type — this is easy to forget because omission doesn't cause test failures, just leaves future Claudes unaware of the capability.

**Google Forms — write side shipped (v0.5.16).** `do(create, doc_type='form')` accepts a YAML or JSON spec and creates a form via Forms API v1. Structurally different from all other `do(create)` paths: two API calls (`forms.create()` for empty form + `forms.batchUpdate()` for questions/description) on `forms.googleapis.com`, not Drive's `files().create()` with media upload. Branches early in `do_create` before any Drive-centric logic. OAuth scope upgraded to `forms.body` (superset of the read-only scope). Key gotcha: item titles cannot contain newlines — API returns 400 "Displayed text cannot contain newlines". `_split_title()` in `tools/form_create.py` handles this by splitting multi-line YAML block scalars into title + description. Form descriptions render as plain text, not markdown — bullet characters appear literally, not as formatted lists.

**Adding a new Google content type is now a recipe:** (1) add scope, (2) add adapter, (3) add extractor, (4) wire elif in router, (5) update 3 MCP resources + understanding.md. The Forms adapter was the fastest content type addition because it followed the architecture's grain.

**Google API error bodies are lost in raise_for_status().** When debugging API failures (especially Forms), the HTTP client's `raise_for_status()` throws before the response body can be read. To see the actual error message, bypass with a raw `httpx.Client` call. Filed as mise-wipopo to fix properly.

Gmail capabilities: search operators exposed as MCP resource, label IDs in data model, live labels directory resource (`mise://gmail/labels`), `list_labels()` in gmail adapter. Write operations (draft, reply_draft, archive, star, label) shipped Feb 2026. Triage docs updated to show `label` covers mark_read/unread/unstar — no separate ops needed (see "generic primitive" principle above). Batch ops (archive/star/label accept `file_id` as `str | list[str]`) and search pagination (follows `nextPageToken`, surfaces `truncated` flag as cue warning) both shipped Apr 2026. Workspace skill updated with full Gmail coverage (dobida). `is_unread` bug fixed — search fields mask was missing `labelIds` (daduti). mise-wiboka outcome complete.

## Cowork & Desktop integration (May 2026)

**Cowork is Claude Code in a VM with `~/.claude/` swapped for a session-scoped temp dir.** When a user uploads a plugin via Customize → Browse plugins → upload, Cowork stages the bundle into `CLAUDE_CONFIG_DIR=/var/folders/.../<id>` for that session. CC's standard plugin loader walks that dir, reads `.claude-plugin/plugin.json`, fires `SessionStart` hooks, launches stdio MCP servers via `mcpServers.*` — all on the **Mac side**, all unchanged from native CC behaviour. Skills, MCP, hooks, and commands all reuse CC's existing machinery.

**The 4-month plugin-MCP gap is closed (2026-05-04).** Earlier handoffs (2026-04-08/09/10/27) framed this as "three extension systems with no shared plumbing" — that framing is now historical. Anthropic stopped trying to parallel-implement plugin loading inside Cowork's runtime; uploaded plugins are now staged into `CLAUDE_CONFIG_DIR` and CC's existing machinery does the work. A single `.claude-plugin/plugin.json`-shaped bundle works for both CC and Cowork; nothing Cowork-specific is needed in the plugin itself. **Diagnostic implication:** when something doesn't land, the failure is in the Mac-side spawn, not in the VM. Don't conclude failure from one early snapshot — stdio handshake takes seconds; check again.

**Mise install paths (all working as of 2026-05-04):**

1. **Cowork uploaded plugin** (the validated path) — `mise-cowork-plugin-v3.zip` built from the repo, uploaded via Customize → upload custom plugin. Validator accepts inline `mcpServers` without `type`; runtime spawns the stdio server on Mac side; tools surface as `plugin:mise:mise/<tool>`. Plugin version 0.6.0.
2. **`claude_desktop_config.json` → `mcpServers`** — Desktop runs stdio on Mac, bridges as a "connector" into Cowork's VM. Desktop proxies; the VM never connects directly.
3. **MCPB extension (`.mcpb` file)** — `manifest.json` with `"server.type": "uv"` for auto-install. Validated `mise-en-space-0.5.13.mcpb` (355KB). New install needs a full Desktop restart.

**What still doesn't work:**
- HTTP MCP from `.mcp.json` pointing at local IPs — gvisor networking blocks host access (`172.16.10.1` unreachable), `allowedDomains=1` restricts egress. Connectors route through Anthropic's cloud, not local network.
- The Cowork VM (Linux ARM64, Python 3.10, ephemeral, MITM proxy) can't run mise stdio server itself — but it doesn't need to; everything is Mac-side.

**Open Cowork-shaped polish (filed in `.bon/quickfile-2026-05-04-cowork-polish.md` because Dolt was offline at filing time):**
- `cues._identity` self-disclosure on every mise response — multi-account Workspace ambiguity (Cowork's native Drive/Calendar connector vs mise) is the highest-leverage fix.
- Workspace SKILL.md still has CC-specific reactivation chatter visible in Cowork's Customize UI.
- Field report: connector disambiguation in Cowork (broader than mise — affects all multi-account users).
- `setup_oauth` unit tests; `auth.py --auto` port-3000 pre-check parity; Cowork plugin platform gating (Windows install would silently fail).

## UX patterns: friendly error wrapper

When an external dependency (OAuth token) is missing, the tool's error response includes a *concrete in-app remediation pointer* — not a CLI command, but the name of another tool the same Claude can invoke. The remediation tool spawns a detached subprocess so the MCP call returns immediately with a URL inline as fallback. The data self-discloses; Claude reads the error, finds the remedy verbatim, calls it.

This pattern showed up twice in the 2026-05-04 install sweep: (1) `mise.do(operation="setup_oauth")` invoked because the error message named it, and (2) the multi-Workspace-connector ambiguity that became the `cues._identity` polish bon. **General principle:** prefer self-disclosing data over instructional copy. Skill copy may not be read; data attached to a response always is.

**Three separate extension systems in Desktop still don't share plumbing** (this part of the older mental model survives):
1. **MCPB extensions** (`Claude Extensions/`) → `LocalMcpServerManager` → MCP tools work, skills ignored
2. **Uploaded plugins** (Cowork-side) → CC's plugin loader → MCP + skills + hooks + commands all work
3. **`claude_desktop_config.json`** → `mcpServers` → MCP tools work, no skills mechanism

**MCPB packaging reference:** `manifest.json` + `pyproject.toml` + source code. `"server.type": "uv"` for auto-install. `mcpb validate` / `mcpb pack` via `@anthropic-ai/mcpb` CLI. `.mcpbignore` excludes dev artifacts. `${__dirname}` for portable paths.

**Cowork plugin architecture (deeper):** `cowork-plugin-shim.sh` (in app bundle) reveals compiled-binary plugins with `cowork_require_token` for credential injection and `cowork_gate` for permission bridging via filesystem IPC. First-party Gmail/Calendar MCPs (`gmail.mcp.claude.com`) are Anthropic-hosted HTTP services.

**Strategic outcome:** Remote deployment path (tokiju → winala → sefepo) parked as someday/maybe — only needed for Claude.ai web without Desktop, or mobile. Cowork-via-Mac is now the path users hit.

Backlog: image/PDF edge cases (mise-heferu), Drive shortcuts (mise-nitaco), keychain token materialisation (mise-zozewa), image embedding privacy (mise-hagaru), calendar forward-looking (mise-milizo), plugin MCP launch gap investigation.
