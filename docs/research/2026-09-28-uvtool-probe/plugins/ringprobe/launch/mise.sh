#!/bin/sh
# Probe launcher (stands in for the ring kit's server command). Finds the mise engine
# that batterie installs as a uv tool: PATH first, then uv's own tool bin dir (for
# surfaces whose PATH omits it). On a fresh machine batterie's SessionStart hook may
# still be installing it, so wait up to 20 s rather than fail — a failed spawn is
# cached by Claude Code as needs-auth and skipped for 15 minutes.
echo "$(date +%s.%N) launcher-start PATH-hit=$(command -v mise-en-space-server || echo none)" >> /tmp/uvtool-probe/order.log
find_uv() { command -v uv 2>/dev/null && return; for c in /usr/local/bin/uv "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/bin/uv; do [ -x "$c" ] && { echo "$c"; return; }; done; }
UV=$(find_uv)
i=0
while [ $i -lt 40 ]; do
  cmd=$(command -v mise-en-space-server 2>/dev/null)
  [ -z "$cmd" ] && [ -n "$UV" ] && cmd="$("$UV" tool dir --bin 2>/dev/null)/mise-en-space-server"
  if [ -x "$cmd" ]; then echo "$(date +%s.%N) launcher-exec $cmd after $i polls" >> /tmp/uvtool-probe/order.log; exec "$cmd" "$@"; fi
  sleep 0.5; i=$((i+1))
done
echo "mise: the Google Workspace engine is not installed. batterie@batterie installs it at session start — restart Claude Code; if it persists run /batterie:update." >&2
echo "$(date +%s.%N) launcher-gave-up" >> /tmp/uvtool-probe/order.log
exit 1
