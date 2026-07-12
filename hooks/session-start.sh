#!/bin/bash
# SessionStart hook: (formerly also copied instructions.md → rules/mise.md).
#
# The rules-shard install now lives SOLELY in ensure-mise.sh, which writes it
# with a per-flavour identity header (mise-tatego/betiko). This hook must NOT
# copy the shard too: it runs after ensure-mise.sh, so a plain copy here would
# clobber the header'd file. Left as a no-op stdin-consumer so the SessionStart
# hook registration in plugin.json stays stable.
# set -euo pipefail  # removed: races with plugin autoUpdate cache swap
cat > /dev/null
exit 0
