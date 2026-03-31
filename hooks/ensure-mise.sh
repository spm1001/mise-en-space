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

# 1. Check uv is available
if ! command -v uv &>/dev/null; then
    ISSUES="${ISSUES}• uv not found — install from https://docs.astral.sh/uv/\n"
fi

# 2. Check dependencies are synced (look for .venv in plugin root)
if [ ! -d "$PLUGIN_ROOT/.venv" ]; then
    if command -v uv &>/dev/null; then
        # Auto-sync — this is safe and idempotent
        uv sync --project "$PLUGIN_ROOT" --quiet 2>/dev/null
        if [ ! -d "$PLUGIN_ROOT/.venv" ]; then
            ISSUES="${ISSUES}• Dependencies not installed. Run: uv sync --project \"$PLUGIN_ROOT\"\n"
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
    ISSUES="${ISSUES}• No Google OAuth token (checked Keychain and token.json). Run: cd \"$PLUGIN_ROOT\" && uv run python -m auth\n"
fi

# If no issues, exit silently
[ -z "$ISSUES" ] && exit 0

cat <<EOF
{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "⚠️ Mise MCP server needs setup:\n\n${ISSUES}\nThe MCP server won't work until these are resolved."}}
EOF
