# Field Report: Google Workspace CLI (gws) Exploration

**Date:** 2026-03-05
**Repo:** https://github.com/googleworkspace/cli
**Installed:** `~/.local/npm-global/bin/gws` v0.4.1 (npm)

## What gws Is

A Rust CLI that auto-generates its entire command surface from Google's Discovery Documents at runtime. No hardcoded API wrappers — when Google adds an endpoint, gws picks it up on next run (24h cache). 25 services, ~18k lines of Rust. Not an official Google product.

**Core architectural idea:** Two-phase parsing. First parse `argv[1]` to identify the service (e.g. `drive`), then fetch that service's Discovery Document, build a `clap` command tree, and re-parse the remaining args against it.

## Two Branches of Value

### Branch 1: Things That Help Us Develop mise

These don't change mise's code — they're workflow improvements for building and debugging.

**Schema introspection (`gws schema`)**
```bash
gws schema drive.files.list          # Shows every parameter with description
gws schema drive.files.list --resolve-refs  # Inlines $ref schemas
```
~300ms. Faster than reading Google's docs pages. Use when building new adapters or debugging unexpected API behaviour.

**Dry-run validation (`--dry-run`)**
```bash
gws drive files list --params '{"pageSize": 5}' --dry-run
# Returns: exact URL, method, query params, body — without sending
```
Shows what an API call actually does before you wire it into Python. Useful for verifying param names and URL templates.

**Client-side body validation**
Before sending, gws validates request bodies against Discovery Document JSON schemas. Immediate feedback on malformed requests rather than waiting for a 400 from Google. We could study this for pre-validating `do()` operations.

**Ad-hoc API exploration**
For APIs mise doesn't cover (Calendar, Tasks, Chat, Admin), gws gives instant access without writing adapters. The meeting prep PoC used `gws calendar events list` because mise has no calendar adapter. ~0.5s per call.

**Apps Script deployment**
```bash
gws apps-script +push --script SCRIPT_ID --dir src
```
Clean replacement for `itv-appscript deploy`. Reads `.gs`/`.html`/`appsscript.json`, builds the Content resource, PUTs to API. Dry-run shows exactly what would be sent. Relevant when we do the gelopa port.

### Branch 2: Things to Incorporate Into mise

These would change mise's code or architecture.

**Discovery-driven validation (unexplored — high potential)**
gws fetches Discovery Documents and uses them to validate inputs *before* sending API calls. mise currently validates at the Python level (hand-written checks in `validation.py`). Could we:
- Fetch Discovery Documents at startup (or cache them)
- Validate `do()` operation params against the schema
- Catch malformed requests before they hit Google
- Auto-generate param documentation from the schema

This is the area we didn't fully explore. The Discovery Documents contain parameter types, required fields, enum values, and descriptions. That's a rich source of truth we're currently hand-maintaining.

**Calendar access for meeting prep (mise-gubaci)**
Two options explored:
1. Shell out to gws — works now, ~0.5s, no new code, but adds binary dependency
2. Thin calendar adapter — consistent with mise architecture, ~100 lines

The calendar data is metadata-only (no content extraction needed), so the adapter would be much thinner than Drive/Gmail adapters.

**Model Armor response sanitisation (unexplored)**
gws can route API responses through Google Cloud Model Armor to detect prompt injection before they reach an AI agent. Two modes: `warn` (annotate) and `block` (reject). If mise ever serves untrusted agents, this matters. Currently theoretical.

**Skill generation structure (unexplored — worth studying)**
gws generates 108 skills from Discovery Documents. The descriptions scored F on our CSO metrics (no timing gates, no triggers, no method preview). But the *organisational structure* is interesting:

| Category | Count | Pattern |
|----------|-------|---------|
| API skills | 48 | One per service, lists resources/methods |
| Helper skills | ~15 | One per `+command` |
| Persona skills | 10 | Role-based (exec-assistant, IT admin, etc.) |
| Recipe skills | 50 | Cross-service workflows (audit-sharing, batch-rename, etc.) |

The `openclaw` metadata convention (category, domain, requires.bins, requires.skills) and the prerequisite chain pattern (`gws-shared` → `gws-drive` → `recipe-audit-sharing`) are worth studying even if we wouldn't auto-generate skills ourselves.

**MCP server mode (unexplored)**
`gws mcp --services drive,gmail` runs a stdio JSON-RPC MCP server. Each API method becomes a tool. We didn't test this. Questions:
- Could `gws mcp --services calendar` be a lightweight calendar MCP?
- How many tools does it register per service? (Potentially hundreds — too noisy?)
- Does the `--helpers` flag limit to just the ergonomic `+commands`?
- What's the tool naming? (`drive_files_list` — flat, could collide)

## Speed Comparison

| Operation | gws | mise |
|-----------|-----|------|
| Drive file list | ~0.9s | ~2-3s |
| Drive search | ~0.8s | ~2-4s |
| Gmail thread list | ~0.6s | ~2-3s |
| Calendar events | ~0.5s | N/A |
| Schema introspection | ~0.3s | N/A |
| Doc export (plain text) | ~1.5s | ~3-5s (markdown + comments + cues) |

gws is 2-3x faster because it's a thin pass-through. The speed gap IS the value gap — mise spends that time extracting, converting, and cueing.

## Auth Pattern

gws accepts a pre-obtained access token via `GOOGLE_WORKSPACE_CLI_TOKEN` env var. Refresh from mise's token:

```bash
export GOOGLE_WORKSPACE_CLI_TOKEN="$(uv run python3 -c "
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import json
with open('token.json') as f:
    t = json.load(f)
creds = Credentials(token=t['token'], refresh_token=t['refresh_token'],
    token_uri=t['token_uri'], client_id=t['client_id'],
    client_secret=t['client_secret'], scopes=t['scopes'])
creds.refresh(Request())
print(creds.token)
")"
```

**Scope gap:** mise's `token.json` doesn't have Apps Script scopes. The slides-formatter token (`~/Repos/itv-slides-formatter/token.slides.json`) does — borrow it for script operations.

## What We Explicitly Didn't Explore

These are the gaps for a follow-up session:

1. **MCP server mode** — `gws mcp --services calendar --helpers`. Is it viable as a calendar adapter? How many tools? What's the DX?
2. **Discovery Document structure** — What exactly is in a Discovery doc? Could we parse it in Python to validate mise inputs?
3. **Pagination behaviour** — `--page-all` streams NDJSON. How does it handle rate limits? Is it usable for large result sets?
4. **Model Armor** — What does sanitised output look like? How does the `--sanitize` flag work in practice?
5. **Workflow helpers in depth** — `+meeting-prep`, `+weekly-digest`, `+email-to-task`. We only ran `+standup-report`.
6. **The `generate-skills` source code** — How does it map Discovery → skill structure? Could we adapt the approach for mise-specific skill generation?

## Source Code Pointers

| File | Lines | What's interesting |
|------|-------|--------------------|
| `src/helpers/script.rs` | ~150 | Apps Script `+push` — reference for gelopa |
| `src/executor.rs` | 1819 | Request construction, multipart uploads, pagination |
| `src/discovery.rs` | ~200 | Discovery Document fetching and caching |
| `src/validate.rs` | 569 | Input validation — path traversal prevention, resource name validation |
| `src/mcp_server.rs` | 470 | MCP tool generation from Discovery Documents |
| `src/generate_skills.rs` | 1152 | Skill generation from Discovery + curated registry |

The cloned repo is at `/home/modha/Repos/gw-cli/`.
