#!/bin/bash
# SessionStart hook: symlink instruction shard into rules/
set -euo pipefail
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(dirname "$HOOK_DIR")"
if [ -f "$PLUGIN_ROOT/instructions.md" ]; then
    mkdir -p "$HOME/.claude/rules"
    ln -sf "$PLUGIN_ROOT/instructions.md" "$HOME/.claude/rules/mise.md"
fi
# Consume stdin (hook protocol)
cat > /dev/null
exit 0
