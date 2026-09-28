#!/bin/bash
# Probe hook (stands in for batterie's mise hook): install the engine as a uv tool
# wherever uv's own config says tools go — exactly as ensure-bon.sh installs bon.
L=/tmp/uvtool-probe/order.log
echo "$(date +%s.%N) hook-start" >> $L
BIN="$(uv tool dir --bin 2>/dev/null)"
if [ ! -x "$BIN/mise-en-space-server" ]; then
  uv tool install -q --force "mise-en-space[extraction] @ file:///tmp/uvtool-probe/dist/mise_en_space-1.87.0-py3-none-any.whl" --with "/home/modha/.claude/plugins/cache/batterie/batterie/2.0.1/mise/wheels/jeton-1.4.1-py3-none-any.whl" >>/tmp/uvtool-probe/install.log 2>&1
  echo "$(date +%s.%N) hook-installed rc=$? bin=$BIN" >> $L
else
  echo "$(date +%s.%N) hook-already-present" >> $L
fi
exit 0
