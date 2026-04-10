# Field report — Cowork plugin MCP gap

**Date:** 2026-04-10
**Context:** Trying to ship mise as a self-contained Cowork `.plugin` upload (not MCPB).
**Outcome:** MCPB is the only working vehicle for stdio MCP servers in Cowork. The `.plugin` upload path has a structural dead zone.

## TL;DR

The `create-cowork-plugin` skill spec documents stdio MCP servers as supported in `.claude-plugin/plugin.json` plugins. In practice, the upload path has a schema mismatch:

| `mcpServers` format | Marketplace validator | Runtime |
|---|---|---|
| `"./.mcp.json"` (string ref) | **Accepts** ✓ | **Ignores** ✗ — never starts the server |
| Inline object, no `type` field | **Rejects** ✗ — generic "Plugin validation failed" | (never reached) |
| Inline object with `"type": "stdio"` | **Rejects** ✗ — same generic toast | (never reached) |

Neither format gets you a running stdio MCP server. The marketplace and the runtime expect different shapes and there's no overlap.

The MCPB format works because it has its own lifecycle handler keyed off `manifest.json` → `"server": { "type": "uv", ... }`. That's the format yesterday's session shipped (`mise-en-space.mcpb`) and it's the right vehicle for a Python/uv stdio server.

## Test matrix (what we tried)

All from the Desktop "Upload local plugin" dialog (Settings → Extensions). Plugin built at `/private/tmp/mise-full-plugin/`. Multiple sessions across 2026-04-09 and 2026-04-10.

| `plugin.json` `mcpServers` | Upload result | Install result | MCP server starts? |
|---|---|---|---|
| `"./.mcp.json"` (string ref) | Upload succeeds | `RemotePluginManager: Installed plugin: mise` | No — never appears in connected servers list |
| Inline object, no `type` field | "Plugin validation failed" toast | — | — |
| Inline object with `"type": "stdio"` | "Plugin validation failed" toast | — | — |

The string-ref version installs cleanly but the MCP server never appears in the session's connected MCP servers (verified via `mcpServerStatus returned 9 servers (8 with tools)` log line — no `mise` in the list). No error logged. Silently dropped.

## Why MCPB works and `.plugin` doesn't

The Remote Plugin Manager (RPM) extracts both formats but processes them differently:

- **MCPB** (`manifest.json` with `"server": { "type": "uv", "mcp_config": { ... } }`): RPM knows about the `uv` server type. It runs `uv sync` (creates `.venv/`, installs deps) before launching. The `mcp_config` is read directly. This is the path "Google Workspace (mise)" uses — visible in `mcp.log` as a fully working MCP server.
- **`.plugin`** (`.claude-plugin/plugin.json` with `mcpServers`): RPM extracts the files, stages them via HostLoop, passes to Claude Code as a `"type": "local"` plugin. Claude Code reads `plugin.json` but doesn't appear to follow the `mcpServers` reference (whether string or inline). Skills load fine. MCP servers don't.

The CLI validator (`claude plugin validate`) is permissive — accepts both inline and string-ref. The marketplace validator is stricter than the CLI. The runtime is stricter than both. Three different schemas, no consistent intersection.

## Files you need in a Python/uv plugin bundle

Discovered the hard way — each missing file surfaced as the next error after fixing the previous one:

| File | Why it's needed |
|---|---|
| `.python-version` | `uv run` defaults to system Python (e.g. 3.14 from Homebrew). Without the pin, transitive deps without 3.14 wheels (`onnxruntime` from `markitdown[pdf]`) fail at install. |
| `README.md` | Hatchling validates `pyproject.toml`'s `readme = "README.md"` field at editable-install time, even when you're only running, not building. |
| `pyproject.toml` + `uv.lock` | Standard. |
| `.venv/` | **Don't** bundle — it's host-specific. `uv run` creates one on first launch. |

The `.mcpbignore` in the mise repo correctly excludes `.venv/`. If you build a `.plugin` bundle separately (not via the MCPB packer), you need an equivalent exclusion list and you must include `.python-version` and `README.md` explicitly.

## Process spawning detail

All MCP server processes spawned by the Desktop app go through `/Applications/Claude.app/Contents/Helpers/disclaimer` — a wrapper for sandboxing/permissions. Visible in `~/Library/Logs/Claude/main.log` as:

```
[error] Failed to read version of python binary "python", failed with Error:
Failed to spawn python (via disclaimer): /Applications/Claude.app/Contents/Helpers/disclaimer
exited with code 1: Failed to spawn process: No such file or directory
```

The disclaimer's PATH includes `/opt/homebrew/bin` (logged as `Spawn PATH ... applied allPaths() floor`), but absolute paths are safer if you hit "command not found" mysteries. `/opt/homebrew/bin/uv` works; bare `uv` works on this machine but might not on someone else's.

## Mental model correction

Yesterday's handoff (`2026-04-09-close.md`) said:

> Plugin MCP servers don't launch in Cowork despite the spec saying they should. ... This feels like a "not yet" — the plumbing is 90% there. Sameer suspects we're missing something rather than hitting a hard wall.

Today's diagnosis upgrades that: it **is** a hard wall. The schema mismatch is structural, not a missing wire. Until the marketplace validator and the runtime agree on a single shape for `mcpServers`, stdio MCP servers in `.plugin` uploads are impossible. The "90% there" intuition was wrong because the missing 10% turned out to be the bridge between two incompatible halves of the system, not a small gap.

(One caveat: we never tried the create-cowork-plugin skill's intended delivery path — writing the `.plugin` file to a session's outputs directory and accepting it via the rich preview. That might bypass marketplace validation. Filed as a Bon to try.)

## Recommendation

For mise specifically: **ship as MCPB, not as `.plugin`**. MCPB is the format with the right lifecycle handler for a Python/uv stdio server. The yesterday-built `mise-en-space.mcpb` is the correct artifact.

For Anthropic: this gap is worth reporting. A user who follows the `create-cowork-plugin` skill literally will produce a plugin that uploads, installs, and silently does nothing. No error, no warning, no log entry — the MCP server is just missing from the connected servers list. Generic "Plugin validation failed" toast on the inline format with no detail.

## Test commands

```bash
# Validate a plugin manifest (CLI — permissive)
claude plugin validate /path/to/plugin/.claude-plugin/plugin.json

# Test that uv can resolve a Python MCP plugin's deps
uv run --project /path/to/plugin python3 -c "from mcp.server.fastmcp import FastMCP; print('ok')"

# Watch what the Desktop app actually does on upload
tail -f ~/Library/Logs/Claude/main.log | grep -i 'plugin\|mcp\|spawn'

# Inspect what the MCPB extracted vs what the .plugin extracted
ls "/Users/modha/Library/Application Support/Claude/local-agent-mode-sessions/*/rpm/plugin_*/"
```
