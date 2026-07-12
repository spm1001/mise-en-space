#!/bin/bash
# SessionStart hook: copy instruction shard into rules/
# set -euo pipefail  # removed: races with plugin autoUpdate cache swap
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(dirname "$HOOK_DIR")"
if [ -f "$PLUGIN_ROOT/instructions.md" ]; then
    mkdir -p "$HOME/.claude/rules"
    # Copy, NOT symlink: the plugin root can be an ephemeral temp dir (Desktop
    # hostloop stages under /var/folders/.../T, which macOS purges) — a symlink
    # there dangles and the shard silently vanishes. This hook re-runs every
    # session-start, so the copy stays current. Do NOT revert to ln -sf.
    cp -f "$PLUGIN_ROOT/instructions.md" "$HOME/.claude/rules/mise.md"
fi
# Consume stdin (hook protocol)
cat > /dev/null
exit 0
