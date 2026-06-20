#!/bin/bash
# SessionStart hook: ensure mise MCP server can start
# Silent when everything is fine; helpful when it's not.

# Symlink instruction shard into rules/
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_PLUGIN_ROOT="$(dirname "$HOOK_DIR")"
if [ -f "$_PLUGIN_ROOT/instructions.md" ]; then
    mkdir -p "$HOME/.claude/rules"
    ln -sf "$_PLUGIN_ROOT/instructions.md" "$HOME/.claude/rules/mise.md"
fi

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
[ -z "$PLUGIN_ROOT" ] && exit 0

ISSUES=""

# Capture uv sync output so a failed dependency install is diagnosable (bon-dotupu).
SYNC_LOG="$HOME/.cache/mise/ensure.log"
mkdir -p "$(dirname "$SYNC_LOG")" 2>/dev/null

# 1. Check uv is available
if ! command -v uv &>/dev/null; then
    ISSUES="${ISSUES}• uv not found — install from https://docs.astral.sh/uv/\n"
fi

# 2. Check dependencies are synced (look for .venv in plugin root)
if [ ! -d "$PLUGIN_ROOT/.venv" ]; then
    if command -v uv &>/dev/null; then
        # Auto-sync — this is safe and idempotent
        uv sync --project "$PLUGIN_ROOT" --quiet >"$SYNC_LOG" 2>&1
        if [ ! -d "$PLUGIN_ROOT/.venv" ]; then
            ISSUES="${ISSUES}• Dependencies not installed (full error: ${SYNC_LOG}). Run: uv sync --project \"$PLUGIN_ROOT\"\n"
        fi
    else
        ISSUES="${ISSUES}• Dependencies not installed (need uv first)\n"
    fi
fi

# 3. Check for OAuth token (Keychain, data dir, or plugin root)
HAS_TOKEN=false
if command -v security &>/dev/null; then
    security find-generic-password -s "mise-oauth-token" -w &>/dev/null && HAS_TOKEN=true
fi
# Plugin data dir (version-stable, where token_store.py actually writes on Linux)
PLUGIN_DATA_DIR="$HOME/.claude/plugins/data/mise-batterie-de-savoir"
if [ "$HAS_TOKEN" = false ] && [ -f "$PLUGIN_DATA_DIR/token.json" ]; then
    HAS_TOKEN=true
fi
# Legacy: plugin root (versioned cache dir)
if [ "$HAS_TOKEN" = false ] && [ -f "$PLUGIN_ROOT/token.json" ]; then
    HAS_TOKEN=true
fi
if [ "$HAS_TOKEN" = false ]; then
    ISSUES="${ISSUES}• No Google OAuth token (checked Keychain and token.json). Easiest fix: ask Claude to call mise.do(operation=\"setup_oauth\") — opens a browser and saves the token automatically. CLI alternative: cd \"$PLUGIN_ROOT\" && uv run python -m auth --auto\n"
fi

# If no issues, exit silently
[ -z "$ISSUES" ] && exit 0

# Render via json.dumps so messages containing quotes (e.g. the quoted PLUGIN_ROOT in
# recovery commands) produce valid JSON — a raw heredoc does not escape them (bon-dotupu).
MSG="⚠️ Mise MCP server needs setup:\n\n${ISSUES}\nThe MCP server won't work until these are resolved."
python3 -c "import json; print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': '''${MSG}'''}}))"
