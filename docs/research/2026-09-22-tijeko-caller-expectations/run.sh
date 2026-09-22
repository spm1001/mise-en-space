#!/usr/bin/env bash
# mise-tijeko: put the scenarios to blank-slate Claudes via trousse's ardoise.
# One subject = one model + one seed (the seed fixes scenario and option order).
# Raw --output-format json lands in runs/; score.py reads it. The model that
# actually answered is read from modelUsage there, never from ardoise's banner.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ARDOISE="$(ls -d ~/.claude/plugins/cache/batterie/trousse/*/ | sort -V | tail -1)scripts/ardoise.sh"
mkdir -p "$HERE/runs"

SUBJECTS=(
  "1 claude-opus-5-5" "2 claude-opus-5-5" "3 claude-opus-5-5"
  "4 claude-fable-5-1" "5 claude-fable-5-1" "6 claude-fable-5-1"
  "7 claude-sonnet-5" "8 claude-sonnet-5"
)

for s in "${SUBJECTS[@]}"; do
  read -r seed model <<<"$s"
  out="$HERE/runs/s${seed}-${model}"
  [[ -s "$out.json" ]] && { echo "skip s$seed (have it)"; continue; }
  (
    uv run --script "$HERE/scenarios.py" prompt "$seed" > "$out.prompt.txt"
    "$ARDOISE" -p --stdin --model "$model" --tools "" --output-format json \
      < "$out.prompt.txt" > "$out.json" 2> "$out.err"
    echo "s$seed $model exit=$?"
  ) &
done
wait
