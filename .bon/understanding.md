# Understanding — mise-en-space

Mise-en-space is a Google Workspace MCP server that gives Claude access to Drive and Gmail through three verbs: search, fetch, and do. The architecture follows a strict layering — extractors (pure functions), adapters (thin API wrappers), tools (MCP wiring) — and this separation is load-bearing, not cosmetic.

## Mass accumulates where mechanical enforcement can't see

The layered code stayed small and pure for a year because `tests/unit/test_architecture.py` policed it. Meanwhile server.py and tools/fetch/gmail.py — outside that test's jurisdiction — quietly grew to 1,318 and 903 lines while CLAUDE.md claimed "server.py just registers tools." An oversized module in a well-policed codebase signals a **jurisdiction gap, not a discipline failure**. The fix is extending the rules, not just cleaning the module — the rules make the cleanup self-maintaining. The jimohe shrink (2026-06-10) did both: server.py 1,318 → 344 lines (resource text → `resources/docs.py`, remote orchestration → `tools/remote.py`, dispatch machinery → `tools/dispatch.py`), and test_architecture.py gained jurisdiction over workspace/, resources/, the root-tier utilities, plus a 500-line cap on server.py itself. When writing such rules, prefer **discovery to enumeration** — a glob picks up new files automatically; a hardcoded list is itself a future jurisdiction gap (FILE_RULES was converted accordingly the same day).

Companion methodology note: when a structural review runs with prior findings as priors (the canumo /toise, report at `docs/toise-2026-06-10.md`), the **refutations are as valuable as the confirmations** — two of five planned hardening actions were down-priced (zedoli's "live bug" premise refuted, all ~20 call sites are single-call; dibudi's `_REQUIRED_PARAMS` table verified correct). That's triage gold, not wasted effort.

## The generic primitive beats the convenience alias

When mise added `archive`, `star`, and `label` as do() operations, `label` was already the superset — archive is `label("INBOX", remove=True)`, star is `label("STARRED")`. A brief proposed adding `mark_read`, `mark_unread`, and `unstar` as three more convenience aliases. But each new operation name costs tokens in three places: tool description (hard-capped at 2048ch), dispatch table, and Claude's reasoning about which op to pick. The right move was zero new operations — just better docs showing that `label` with system label names covers all triage actions.

**Before adding a new do() operation, check whether an existing operation already handles it with different parameters.** The description ceiling makes this more than aesthetics — it's a resource constraint.

When you *do* add one: the op name and count drift more surfaces than CLAUDE.md's 6-step recipe lists. `test_dispatch.py` auto-verifies OPERATIONS/DISPATCH/REQUIRED_PARAMS sync, but the "N ops: a, b, c…" line lives un-policed in CLAUDE.md, README.md, `DO_DESCRIPTION_FULL` in tools/dispatch.py, `docs_do()` in resources/docs.py, and the SKILL.md operations table. Grep for the current count (`grep -rn "ops\b"` on the op list) and update every surface — otherwise cold-start docs say "14 ops" while the tool has 15 (learned adding comment_reply, 2026-06-28).

## The MCP description length ceiling

MCP tool descriptions have a hard ceiling at 2048 characters, enforced by Claude Code's `MAX_MCP_DESCRIPTION_LENGTH` constant at `src/services/mcp/client.ts:218`. Descriptions over this limit are sliced and suffixed with `'… [truncated]'`. This truncation triggers a secondary, more insidious failure: the Anthropic API silently drops properties from the tool's JSON schema during `tool_reference` expansion (the mechanism that expands deferred tools when ToolSearch is called). The schema is sent in full by CC — the property loss happens API-side during expansion, not during transmission.

**Diagnostic signature:** ToolSearch returns fewer properties than `mcp._tool_manager._tools[name].parameters` reports. The dropped property appears nowhere in the ToolSearch result — not truncated, just absent. When Claude emits a tool_use including the missing property, the API strips it before CC sees the tool_use block, so CC's `z.object({}).passthrough()` never gets the chance to help.

**Fix within MCP server control:** Keep tool descriptions short and put detail in MCP resources (`mise://tools/*`). The `do()` description went from a full Args/Returns docstring (2494ch) to a compact operation summary (~600ch). All 21 properties survived immediately. Don't expand descriptions without checking `len(tool.description) + len(json.dumps(tool.parameters))` against practical limits. This applies to all batterie MCP servers, not just mise.

## Remote mode architecture

The remote mode (StreamableHTTP for Claude.ai connectors) was designed around the principle of *not touching the fetchers*. The 10+ type-specific fetch functions in `tools/fetch/` are battle-tested and complex. Rather than making them remote-aware, the orchestrators intercept after the fetch completes: deposits go to a temp dir, content is read back and included inline in the response, then the temp dir is cleaned up. This "post-hoc read-back" pattern means all fetchers work unchanged in both modes. The same applies to search — `SearchResult.to_dict()` already had an inline mode (`path=None`), so remote just clears the path after deposit. The orchestrators live in `tools/remote.py` (moved from server.py in the mise-jimohe shrink, June 2026); when extending for new content types, keep the interception there, never inside the fetchers themselves.

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

## Guest mode — caller-owned credentials (mise-lebapo, 0.7.8; mise-kivane, 0.7.9)

Set `MISE_TOKEN_PATH` and mise runs as a guest: it loads the caller-owned token file at that path, never writes back, and persists nothing of its own (no Keychain, no plugin-dir fallback). This is the path for an embedding host (Cornichon) that owns its own credential lifecycle and just wants mise to borrow a token for the call.

Three sharp edges shaped it. (1) Guest tokens are often ADC-shaped (application-default credentials), not mise's own OAuth `token.json` — lebapo loads ADC-shaped files directly rather than assuming mise's schema. (2) When the credentials carry a quota project, mise sends `x-goog-user-project` so billing/quota attributes to the *caller's* project, not mise's. (3) A guest token may carry no Gmail scope, so guest mode defaults an omitted `search` source list to `['drive']` (kivane) — a Gmail search on a Drive-only token would otherwise fail confusingly.

Separately, kivane made `do(move)`'s target folder `folder_id` (canonical, matching `do(create)`); `destination_folder_id` is a kept deprecated alias.

## Google Sheets: the merged-cell trap

Google's Sheets value APIs (`values.batchGet`) return *display* data, not *structural* data. Merge ranges live in the metadata endpoint (`spreadsheets.get`), not the value endpoint. When you fetch values with `FORMATTED_VALUE`, merged cells return the value only in the top-left cell; every other cell in the merge range comes back as an empty string.

The fix: request `sheets.merges` in the metadata fields, `_resolve_merges` propagates top-left values post-fetch. `_resolve_merges` handles horizontal merges too — merged column headers get the same value in each spanned column. The warning cue mitigates misinterpretation.

## httpx migration — complete (Mar 2026)

All adapters and tools use sync httpx. Phase 2 (async) is future work. Key patterns: `MiseSyncClient` and `MiseHttpClient` are intentional near-duplicates (MiseSyncClient dies in Phase 2). Google API URLs are hardcoded constants. (`services.py`, once kept as dead code for integration test scaffolding, has since been deleted entirely.)

## Build flavours: full vs slim (mise-hibere, 0.7.9)

Mise ships in two shapes from one codebase. The **full** build (dev, CI, and the marketplace plugin) carries local extraction — `markitdown[pdf]` + `pdf2image` — for fast local PDF text, HTML→markdown, and PDF page thumbnails. The **slim** build (what Cornichon vendors) installs plain core: `markitdown` is absent, so `adapters/pdf.py` degrades to Drive server-side conversion, `html_convert.py` to tag-stripping, and PDF thumbnails are skipped. Image fetch still works because `pillow` is core.

The mechanism is the load-bearing part: the heavy deps live in an optional `extraction` extra, and the "full" choice rides in the *spawn* command (`uv run --extra extraction`), not the install command. The plugin's mcpServers args spawn with the extra and the assembler vendors plugin.json — so the marketplace stays full automatically, while an embedding consumer that installs plain core gets slim. You can't make an extra on-by-default, so "default install = slim, full opts in" is forced; routing the opt-in through plugin.json means zero estate-wide install-command churn.

Two working consequences: (1) run the test suite with `--extra extraction` (or `--all-extras`) — PDF-extraction tests assume markitdown is present and fail in a slim env; (2) the slim PDF→Drive fallback needs Drive *write* scope (it uploads to convert), fine for Cornichon's own-Drive PDFs but it would break under a read-only guest token.

What made slim cheap rather than crippling was profiling what the weight actually buys. The 67M monster was onnxruntime, pulled by magika — markitdown's ML byte-level file-type *sniffer*. Mise never needs it: it routes by Drive MIME metadata, not by sniffing bytes. Dropping markitdown sheds ~84M and costs only fast-local-PDF and PDF thumbnails. The general lesson: before declaring a dependency load-bearing, check whether its expensive part answers a question you've already answered another way.

Watch the spawn arg: if `--extra extraction` is ever dropped from plugin.json, every plugin/Cowork user silently goes slim (PDF round-trips Drive, no thumbnails). The assembler vendors plugin.json so the arg travels — don't strip it.

Direction-of-conversion maps to build flavour. HTML→markdown (markitdown) is extraction-extra; markdown→HTML (`markdown_to_html` in html_convert.py, python-markdown) had to go in **core**, because `draft`/`reply_draft` are remote-safe ops that must render GFM in every flavour including slim (zolowa, 2026-06-28). Rule of thumb: anything a remote-safe op needs is core; local fast-path extraction is the extra.

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

## Field reports outrank synthetic tests

When a field report names the file, the lines, the root cause, and a fix sketch, it is grounded in real-world reproduction. Existing unit tests are often snapshots of synthetic shapes from one fixture. If the brief's recommended fix breaks a test, read the test before hunting for an alternative fix — usually the test asserts a *more aggressive* version of "correct" behaviour, and the field report has just shown that aggression eats real content. The right move is to update the test to the new contract, not to find a clever fix that preserves the test's shape.

Concrete case (May 2026, zojoma): the brief said "walk backwards" for signature stripping. That broke `test_strips_corporate_contact_block`, so a proximity-window alternative was tried instead — it preserved the test but didn't fix the standalone-message case the brief also described. Walking backwards + updating the test was right all along. The test was a captured snapshot; the field report is the contract.

**Operational corollary for multi-commit sessions:** this repo is public and Sameer commits to it directly — `git fetch && git log HEAD..origin/main` before starting. The MCPB drop once landed upstream mid-session and a version bump on the deleted `manifest.json` nearly shipped; it was caught only by a rejected push.

## Observability and stock-taking

The call log (`~/.local/share/mise/calls.jsonl`) is the primary operational data source. A stock-taking session (Apr 2026, ~375 calls over 19 days) revealed patterns invisible from feature work alone: fetch 47%, search 33%, do 20%. Gmail edges out Drive as most-searched source. `replace_text` is the top do() op. Six ops have zero real-world usage (draft, rename, share, prepend, star, label). Activity and calendar search sources are dormant. 99% success rate. The lesson: **periodically step back from building to observe how the thing is actually used** — the observation often matters more than the next feature.

Code health (same session): 92% test coverage, zero layer violations, dispatch table sync verified, tool descriptions within 2048 limit, no TODO/FIXME/HACK. Mypy errors 30→22 (remaining are upstream httpx/orjson noise — the right fix is a thin `_parse_json` wrapper centralising one ignore, not scattershot `type: ignore`).

## MCP Resources: what they're for

Mise ships eight Resources via FastMCP — six static docs (`mise://docs/*`), one live API call (`mise://gmail/labels` → `Gmail labels.list` rendered as markdown), and one parameterised template (`mise://tools/{tool_name}` for auto-generated tool docs). Empirical behaviour (Apr 2026, tested in a live CC session):

- `ListMcpResourcesTool` returns the seven concrete URIs. **Parameterised templates are invisible to discovery** — `ReadMcpResourceTool` resolves them fine if you construct the URI yourself, but listing won't surface them.
- `ReadMcpResourceTool` returns content **inline** in the tool result. Cold-fetching via Resource therefore dumps the whole payload into context — fine for tiny live state, architecturally wrong for content (a 50-page PDF would torch the window).

**The right division of labour:** Resources for small live introspection (label lists, drafts, quota); the existing `fetch` verb (writing to disk) for content acquisition; native filesystem read for steady-state. The URI scheme is **not** load-bearing — `{type, id}` carries everything URIs were carrying. The web URL (`docs.google.com/document/d/abc`) is the human-facing handle if anyone needs one; nothing requires inventing a new scheme. Lesson worth generalising: when an architectural layer X exists "because of Y," and Y gets cut, audit X — it usually goes too.

`mise://gmail/labels` is a model worth copying for other small live state (drafts list, recent activity, drive quota). All small, ambient, no cold-blob problem.

## Workspace cache vision (cuvusa) — ABANDONED 2026-07-07, design record only

**Sameer's call (2026-07-07): the central cache is not worth it — do not build this.** The idea had already been lost and re-found ~3 times; this is the deliberate end of the line, not another loss. What survives: deposit *disposal* lives on as **mise-bigiko** (gc verb, on the board under novanu, possibly plus a rename of the deposit dir to a hidden name), and the error-message slice of kaceta already shipped as **mise-dizupe**. Everything else below is a design record kept only in case a real consumer (a genuine dedup or corpus-grep need) ever revives it. If you're reading this wondering whether to build it: don't — re-open the conversation with Sameer instead.

Background: 20 `mise/` folders are scattered across `~/Repos/`, ~250MB total — Drive content gets pulled per-cwd, never deduplicated, never grep-able as a corpus. Pre-Dolt history (mise-6bo, mise-fuSepi, three open cleanup bons that never shipped) shows this conversation has happened multiple times.

Survivor design after pushback: central cache + cwd hardlinks + manifest index. Four sub-actions under **mise-cuvusa**:
- **mise-rocume** — workspace manager writes to central cache (`~/.mise/cache/`), cwd `mise/` becomes hardlinks
- **mise-diwosi** — cache has a discoverable index; fetch checks it before round-tripping
- **mise-kaceta** — fetch input is pimuga-safe (explicit type, no silent misroute — e.g. `fetch('gmail:abc')` actually routing to gmail instead of silently going to Drive and 404-ing). *The cheap error-message slice of this shipped as **mise-dizupe** (2026-06-28): `detect_fetch_input_problem()` in validation.py pre-checks the two shapes agents fumble (12-char deposit-prefix; non-fetchable URL) and returns a teaching error. The full typed-input redesign is still unbuilt — dizupe documents the failure shapes it would formalize, doesn't conflict with it.*
- **mise-gotace** — cache cleanup in one place (TTL or LRU)

**Sequencing risks:** if rocume ships before diwosi, fetch keeps round-tripping during the gap (decide deliberately). If rocume + diwosi ship before gotace, the cache accumulates without bound — ship gotace concurrently or document the manual cleanup path. Hardlinks need cross-filesystem fallback to symlinks (Taildrive mounts, external drives) — verify `os.link` failure handling actually triggers fallback. Before implementing rocume, verify Drive's web URL is the right human-facing handle to carry in the manifest.

## Current state (June 2026)

*Caveat on this whole section: current-state prose rots fastest — on 2026-06-10 the morning's own synthesis described as open three things the afternoon fixed. Claims below are dated where possible; for live status, trust the bon board over this prose.*

*(2026-07 update: mise's own version number ended at 0.7.12 — the suite cutover happened and mise now ships under the single Batterie number, 1.2.x. The 2026-06-28 coordinated ship (suite 1.2.2, commit 7d8855f) landed dizupe + zolowa + tojuji; dopufo's wheel-closure fix and the deferred Dependabot bumps followed. zolowa/tojuji are code-shipped but their bons stay open pending live smoke-test.)*

Core MCP server stable and in daily use via stdio (v0.7.12 — 0.7.5 carried the 2026-06-10 fixes below; 0.7.6/0.7.7 were packaging-only bumps during the marketplace husk repair, a legitimate pattern: the version ratchet demands a bump whenever vendored content changes, even when no code did). The 0.7.8→0.7.12 run was the guest-mode arc and its housekeeping: **0.7.8** shipped guest credentials (lebapo — `MISE_TOKEN_PATH`, ADC-shaped load, `x-goog-user-project`); **0.7.9** the guest-mode cluster (kivane folder_id + Drive-only search default, hibere slim build); **0.7.10** docs-only (the build-flavours section, a ratchet bump for vendored CLAUDE.md); bon-dotupu fixed the `ensure-mise` hook (valid-JSON failure render + diagnosable `uv sync`); **0.7.12** swept out decommissioned garde-manger references post-cutover. Cowork plugin validated end-to-end (2026-05-04 field test). **MCPB packaging retired** (May 2026 — `manifest.json` deleted; the Claude Code plugin format is the only bundle now). Remote mode transport done. Merged-cell resolution, pageless doc creation, folder creation, token diagnostics, and heading extraction all shipped. MCP description length fixed (property drop bug resolved). Apps Script email extractor ported from archived repo.

**Gmail trust fixes (v0.7.3, May 18):** signature stripping now walks *backwards* from end-of-body (forward-walking false-positived on benign short text with URL density bleeding from quoted Outlook replies below); `strip_signature_and_quotes` returns `(body, warnings)` and >80% reductions emit a warning. That warning fires on legitimately-short replies with long corporate sigs — semantically correct, but watch for warning fatigue; don't tune the threshold without usage data. Apple-Mail-style bare-name lines survive stripping *by design* (under-stripping is safer than eating content) — "mise didn't strip Alice's name" is the trade, not a bug. Participants now walk From+To+Cc+Bcc across all messages (`_extract_participants`), and Outlook's `application/octet-stream` mis-tagging is recovered by filename extension (`_resolve_attachment_mime`). v0.7.4 (June 10) closed the two follow-ups: participants dedup on canonical email with best display form (mise-nucupi), and the eager thread-fetch path resolves octet-stream MIME the same way fetch_attachment does (mise-dazode).

**v0.7.5 trust fixes (2026-06-10):** `_parse_headers` now canonicalises header-name case (RFC 5322 — Outlook emits `CC:`, which the old case-sensitive filter silently dropped, starving cc lists AND participants); `do_reply_draft` passes `current_user_email()` into reply_all inference, so self-exclusion actually fires (mise-lurumu — both bugs verified live against the field-report thread). And .docx fetches now warn when Word markup was flattened: `extractors/docx_markup.py` counts w:ins/w:del/comments/inline-images via regex-on-bytes (no XML parse — email attachments are untrusted input, regex can't be entity-bombed) and `adapters/office.py` emits cue warnings with counts + authors (mise-lojazo, the kecigu MVP — verified against the actual misleading UA document: 381 changes, 25 comments now announced). Kecigu's remaining faces: email cid-images warn nothing, the Gmail pre-exfil docx path (server-side copy, no local bytes) skips inspection, full redline mode if warnings prove insufficient.

**gmail.py split (wugehi, 2026-06-10):** tools/fetch/gmail.py is 557 lines of two orchestrators; the three concerns live in siblings — `gmail_participants.py`, `gmail_exfil.py`, `gmail_attachments.py`. `classify_attachment()` in gmail_attachments is the single source of MIME→category dispatch (was triplicated and drifting). Test patch targets for the moved helpers point at `tools.fetch.gmail_attachments.*`; the `tools.fetch` facade keeps all public imports stable. The toise's "three return shapes" claim dissolved on inspection — the helper dicts were only ever visible as orchestrator returns because the single file blurred the boundary.

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

**Backlog shape (as of 2026-06-10 evening close):** the board was verified and consciously triaged during the 586-item estate audit (2026-06-09/10) — don't re-triage it. After the day's six closes: **mise-novanu** has ceroru (adapter error contract — recon already showed `tools/fetch/router.py:75-79` catches MiseError+ValueError+Exception uniformly, so option (b) document-the-two-tier-reality likely wins; ~an hour), zedoli (preventive, down-priced), and bigiko (deposit gc — mind the latent overlap with cuvusa/gotace before designing). **mise-zolowa** is the lone untouched field report (draft markdown→HTML; `_content_to_html` in tools/draft.py:96 is a deliberate decision to revisit, one function feeds both paths). **mise-jukalo** (5 open) is now rollout-critical: tideri (Windows gating) and didage (auth honesty) gate the ITV invitation. Standalone: dizupe (fetch error legibility, data-backed from calls.jsonl mining), jikibi (YAGNI-deferred). Closed today with verification: lurumu, dibudi (refuted-by-data), makari, lojazo/kecigu-MVP, wugehi, hinawe.

**Marketplace/deployment reality (post-husk, 2026-06-10):** mise reaches users via the single assembled marketplace `spm1001/batterie` (`claude plugin marketplace add spm1001/batterie`) — CLI + personal Desktop + Cowork. `batterie-de-savoir` stopped being a marketplace at the bds-bajibo cutover (2026-06-10). The claude.ai org/Teams Directory is NOT a live surface: the repo is deliberately public, org marketplaces require a private/internal repo, so that registration was removed 2026-06-20 (bds-kanuve). The assembler vendors mise's FULL runtime source (server.py, pyproject, uv.lock, all modules) because mise declares mcpServers — the 2026-06-10 husk shipped a plugin.json pointing at files that were never vendored, and every Cowork session got a dead server (diagnosis: notes raw/2026-06-10-mise-cowork-husk-diagnosis.md). Release consequence for THIS repo (updated 2026-07: **suite-managed versioning**, bds-matelu): the suite carries ONE version number; mise's own `plugin.json` version is local-dev-only — the assembler stamps every published plugin to the suite version. Release via `/batterie:publish` from this working tree (bumps the suite centrally, ships the 2-repo push); never hand-bump plugin.json to release, never hand-run the assemble. The vendor boundary is *wider than runtime code*: batterie vendors `CLAUDE.md`, `README.md`, `CHANGELOG.md`, `skills/`, `pyproject.toml`, `uv.lock`, `instructions.md`, `credentials.json`, `hooks/`, `apps-script/` too. The ONLY free-to-commit-unbumped trees are `tests/`, `docs/`, and `.bon/` — a "docs fix" to CLAUDE.md is ship-coupled, not free. Verify the live list with `ls ~/repos/spm1001/batterie/plugins/mise/` before assuming any root file is safe to commit standalone. The assembler's guards (entry-point invariant, capability-dir parity, version ratchet) make husk-class failures build-time FAILs, but they live in batterie, not here. The uploaded-zip Cowork path (v3.zip, 0.6.0) is historical — superseded by the marketplace channel.

**The shared bus is a single point of failure (bit live 2026-06-17).** Because the ratchet's blanket `exit 1` fails the *whole* assemble, any one source repo's content-change-without-a-bump wedges the entire marketplace — every plugin stops publishing. On 2026-06-17 a sibling repo (bon, an unbumped CLAUDE.md edit — *not* "vendored docs drift", a wrong cause that propagated through three artefacts before substrate-checking corrected it) blocked mise 0.7.9 and all others for hours. Two durable reflexes: (1) when a marketplace publish fails, check whether a *sibling* repo is the cause before hunting in your own; (2) a bump clears the ratchet *regardless* of which content changed, so the fix working does not validate the causal story — read the copy-list and the publish diff before encoding a cause. The fix-in-flight is **bds-pujaki** (batterie-de-savoir board): quarantine the laggard to its last-good version, publish the rest, then go red to flag it — isolate the blast radius without a silent skip. The 07:00 UTC daily cron is the backstop until it lands.
