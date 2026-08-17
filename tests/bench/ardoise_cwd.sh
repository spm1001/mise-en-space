#!/usr/bin/env bash
# ardoise-cwd.sh — spec2 experiment copy of trousse's ardoise.sh
# (source: spm1001/trousse scripts/ardoise.sh @ 51ab8e3), with ONE change:
# print mode accepts --cwd DIR and cds there instead of the hardcoded /tmp.
# Reason: /tmp/.mise on this host holds another live session's REAL mise
# deposits; the spec is synthetic-only, so subjects must run in a clean CWD.
# ardoise.sh — Run Claude on a blank slate
#
# Creates an isolated HOME with auth + onboarding state but no config,
# skills, hooks, or plugins. Two modes:
#
#   Interactive (default):  Opens Claude TUI as a fresh user would see it.
#   Print (-p):             Runs claude -p for automated/scripted use.
#
# Usage:
#   ardoise.sh                                    # Interactive, CWD=/tmp
#   ardoise.sh /path/to/dir                       # Interactive, specific dir
#   ardoise.sh -p "prompt"                        # Print mode, one-shot
#   ardoise.sh -p --max-turns 5 -- "prompt"       # Print mode with flags
#   echo "prompt" | ardoise.sh -p --stdin         # Print mode, stdin
#   ardoise.sh --home ~/sbx /path/to/dir          # Persistent HOME (multi-step)
#   ardoise.sh --keep -p "prompt"                 # Temp HOME, printed, not deleted
#
# --home DIR : reuse (and keep) a named sandbox HOME across invocations — the
#              recipe for multi-step tests (marketplace add -> install -> verify).
#              Seeded once; reused as-is thereafter (accumulated state survives).
# --keep     : temp HOME as usual, but skip the cleanup trap and print its path.
#
# Punching named holes in the isolation wall (the Vertex passthrough pattern,
# generalised — for eval harnesses that must hand the inner Claude a fixture):
# --env KEY=VAL      : pass one env var through env -i (repeatable).
# --path-prepend DIR : prepend DIR to the inner PATH (repeatable) — e.g. a
#                      directory holding a fixture shim binary. The inner PATH
#                      is otherwise rebuilt minimal; caller's PATH never leaks.
#
# Vertex: if CLAUDE_CODE_USE_VERTEX=1 in the caller's env, the Vertex config
# (project, region, model ids) and gcloud ADC are passed through the isolation
# wall, so ardoise works on (and bills to) a Vertex setup instead of silently
# falling back to Anthropic-API credentials. Auto-detected; no flag needed.
#
# Cross-platform (Linux + macOS).

set -euo pipefail

# ── Parse args ────────────────────────────────────────────────────────

PRINT_MODE=false
CLAUDE_ARGS=()
PROMPT=""
USE_STDIN=false
START_DIR=""
SANDBOX_HOME_ARG=""
KEEP=false
EXTRA_ENV=()
PATH_PREPENDS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--print)
            PRINT_MODE=true
            shift
            ;;
        --home)
            if [[ $# -lt 2 ]]; then
                echo "Error: --home requires a directory path" >&2
                exit 1
            fi
            SANDBOX_HOME_ARG="$2"
            shift 2
            ;;
        --keep)
            KEEP=true
            shift
            ;;
        --cwd)
            if [[ $# -lt 2 || ! -d "$2" ]]; then
                echo "Error: --cwd requires an existing directory" >&2
                exit 1
            fi
            START_DIR="$(cd "$2" && pwd)"
            shift 2
            ;;
        --env)
            if [[ $# -lt 2 || "$2" != *=* ]]; then
                echo "Error: --env requires KEY=VAL" >&2
                exit 1
            fi
            EXTRA_ENV+=("$2")
            shift 2
            ;;
        --path-prepend)
            if [[ $# -lt 2 ]]; then
                echo "Error: --path-prepend requires a directory path" >&2
                exit 1
            fi
            if [[ ! -d "$2" ]]; then
                echo "Error: --path-prepend directory does not exist: $2" >&2
                exit 1
            fi
            PATH_PREPENDS+=("$(cd "$2" && pwd)")
            shift 2
            ;;
        --)
            shift
            PROMPT="${1:-}"
            [[ -n "$PROMPT" ]] && shift
            break
            ;;
        --stdin)
            USE_STDIN=true
            shift
            ;;
        --max-turns|--output-format|--system-prompt|--tools|--model|--allowed-tools|--allowedTools|--append-system-prompt|--permission-mode)
            if [[ $# -lt 2 ]]; then
                echo "Error: $1 requires a value" >&2
                exit 1
            fi
            CLAUDE_ARGS+=("$1" "$2")
            shift 2
            ;;
        --dangerously-skip-permissions)
            CLAUDE_ARGS+=("$1")
            shift
            ;;
        -*)
            CLAUDE_ARGS+=("$1")
            shift
            ;;
        *)
            if [[ "$PRINT_MODE" == true && -z "$PROMPT" ]]; then
                PROMPT="$1"
            elif [[ "$PRINT_MODE" == false && -z "$START_DIR" ]]; then
                START_DIR="$1"
            else
                PROMPT="$1"
            fi
            shift
            ;;
    esac
done

START_DIR="${START_DIR:-/tmp}"

# Validate: print mode needs a prompt or stdin
if [[ "$PRINT_MODE" == true && "$USE_STDIN" == false && -z "$PROMPT" ]]; then
    echo "Usage: ardoise.sh -p [claude-flags] [--] \"prompt\"" >&2
    echo "       echo \"prompt\" | ardoise.sh -p --stdin [claude-flags]" >&2
    exit 1
fi

# ── Find claude binary ───────────────────────────────────────────────

CLAUDE_BIN=$(command -v claude 2>/dev/null) || {
    echo "Error: claude not found in PATH" >&2
    exit 1
}

CLAUDE_REAL=$(readlink -f "$CLAUDE_BIN")

# ── Detect Vertex (before the env -i wall scrubs it) ─────────────────

ON_VERTEX=false
VERTEX_VARS=()
if [[ -n "${CLAUDE_CODE_USE_VERTEX:-}" ]]; then
    ON_VERTEX=true
    for v in CLAUDE_CODE_USE_VERTEX ANTHROPIC_VERTEX_PROJECT_ID CLOUD_ML_REGION \
             ANTHROPIC_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL \
             ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL; do
        [[ -n "${!v:-}" ]] && VERTEX_VARS+=("$v=${!v}")
    done
    # Vertex auth is gcloud ADC. env -i + a fresh HOME hides the default
    # ~/.config/gcloud path, so pin GOOGLE_APPLICATION_CREDENTIALS to the real ADC.
    ADC="${GOOGLE_APPLICATION_CREDENTIALS:-$HOME/.config/gcloud/application_default_credentials.json}"
    [[ -f "$ADC" ]] && VERTEX_VARS+=("GOOGLE_APPLICATION_CREDENTIALS=$ADC")
fi

# ── Find credentials ─────────────────────────────────────────────────

REAL_CLAUDE_DIR="$HOME/.claude"
REAL_CREDS="$REAL_CLAUDE_DIR/.credentials.json"
# Anthropic-API creds are required UNLESS we're on Vertex (which auths via ADC).
if [[ "$ON_VERTEX" == false && ! -f "$REAL_CREDS" ]]; then
    echo "Error: No credentials at $REAL_CREDS" >&2
    echo "Run: claude login   (or set CLAUDE_CODE_USE_VERTEX=1 for a Vertex setup)" >&2
    exit 1
fi

# Real CC config: $HOME/.claude.json (home dir). Fall back to the legacy
# ~/.claude/claude.json seen on some setups.
REAL_CLAUDE_JSON=""
for candidate in "$HOME/.claude.json" "$REAL_CLAUDE_DIR/claude.json"; do
    [[ -f "$candidate" ]] && { REAL_CLAUDE_JSON="$candidate"; break; }
done

# ── Build isolated HOME ──────────────────────────────────────────────

if [[ -n "$SANDBOX_HOME_ARG" ]]; then
    # Persistent, named HOME — reused across invocations, never auto-deleted.
    SANDBOX_HOME="$SANDBOX_HOME_ARG"
    mkdir -p "$SANDBOX_HOME"
else
    SANDBOX_HOME=$(mktemp -d)
    if [[ "$KEEP" == true ]]; then
        echo "ardoise: sandbox HOME kept at $SANDBOX_HOME" >&2
    else
        trap 'rm -rf "$SANDBOX_HOME"' EXIT
    fi
fi

# Seed only a fresh sandbox. A reused --home that's already been seeded keeps
# its accumulated state (installed plugins, added marketplaces) — that's the point.
if [[ -e "$SANDBOX_HOME/.claude.json" || -f "$SANDBOX_HOME/.claude/.credentials.json" ]]; then
    :  # already seeded — reuse as-is
else
    mkdir -p "$SANDBOX_HOME/.claude"

    # Auth (Anthropic API): copy the creds file if present. On Vertex-only setups
    # there's none, and ADC (passed through below) does the auth instead.
    [[ -f "$REAL_CREDS" ]] && cp "$REAL_CREDS" "$SANDBOX_HOME/.claude/.credentials.json"

    # claude.json — copy real one (onboarding state + feature flags), strip project data
    if [[ -n "$REAL_CLAUDE_JSON" ]]; then
        cp "$REAL_CLAUDE_JSON" "$SANDBOX_HOME/.claude/claude.json"
        python3 - "$SANDBOX_HOME/.claude/claude.json" "$START_DIR" << 'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    cfg = json.load(f)
for key in ["projects", "githubRepoPaths", "skillUsage", "clientDataCache",
            "groveConfigCache"]:
    cfg.pop(key, None)
cfg["officialMarketplaceAutoInstallAttempted"] = True
cfg["officialMarketplaceAutoInstalled"] = False
start_dir = sys.argv[2] if len(sys.argv) > 2 else "/tmp"
cfg["projects"] = {
    start_dir: {
        "allowedTools": [],
        "mcpContextUris": [],
        "mcpServers": {},
        "enabledMcpjsonServers": [],
        "disabledMcpjsonServers": [],
        "hasTrustDialogAccepted": True,
        "hasCompletedProjectOnboarding": True,
        "projectOnboardingSeenCount": 0,
        "hasClaudeMdExternalIncludesApproved": False,
        "hasClaudeMdExternalIncludesWarningShown": False,
    }
}
with open(sys.argv[1], "w") as f:
    json.dump(cfg, f, indent=2)
PYEOF

        # Claude reads config from $HOME/.claude.json (symlink), not .claude/claude.json
        ln -s "$SANDBOX_HOME/.claude/claude.json" "$SANDBOX_HOME/.claude.json"
    fi
fi

# ── Construct minimal PATH ───────────────────────────────────────────

CLAUDE_DIR=$(dirname "$CLAUDE_REAL")
CLEAN_PATH="${CLAUDE_DIR}:/usr/local/bin:/usr/bin:/bin"

SYMLINK_DIR=$(dirname "$CLAUDE_BIN")
if [[ "$SYMLINK_DIR" != "$CLAUDE_DIR" ]]; then
    CLEAN_PATH="${SYMLINK_DIR}:${CLEAN_PATH}"
fi

# Caller-requested prepends go first, in the order given (last flag wins the
# front slot by iterating in reverse).
if [[ ${#PATH_PREPENDS[@]} -gt 0 ]]; then
    for ((i=${#PATH_PREPENDS[@]}-1; i>=0; i--)); do
        CLEAN_PATH="${PATH_PREPENDS[$i]}:${CLEAN_PATH}"
    done
fi

# ── Env vars ──────────────────────────────────────────────────────────

ENV_VARS=(
    HOME="$SANDBOX_HOME"
    PATH="$CLEAN_PATH"
    CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
    CLAUDE_CODE_DISABLE_AUTO_MEMORY=1
    ENABLE_CLAUDEAI_MCP_SERVERS=false
)
# Pass Vertex config + ADC through the isolation wall (auth only, like the creds copy).
[[ ${#VERTEX_VARS[@]} -gt 0 ]] && ENV_VARS+=("${VERTEX_VARS[@]}")
# Caller-requested --env passthroughs (explicit, named holes only).
[[ ${#EXTRA_ENV[@]} -gt 0 ]] && ENV_VARS+=("${EXTRA_ENV[@]}")

# ── Run ───────────────────────────────────────────────────────────────

if [[ "$PRINT_MODE" == true ]]; then
    # Print mode: claude -p, TERM=dumb, CWD=/tmp
    CMD_PARTS=(claude -p)
    if [[ ${#CLAUDE_ARGS[@]} -gt 0 ]]; then
        CMD_PARTS+=("${CLAUDE_ARGS[@]}")
    fi

    if [[ "$USE_STDIN" == true ]]; then
        env -i "${ENV_VARS[@]}" TERM="${TERM:-dumb}" \
            bash -c 'cd "$1" && shift && exec "$@"' _ "$START_DIR" "${CMD_PARTS[@]}"
    else
        # "--" ends claude's option parsing: variadic flags (--allowed-tools,
        # --tools) would otherwise swallow the positional prompt.
        env -i "${ENV_VARS[@]}" TERM="${TERM:-dumb}" \
            bash -c 'cd "$1" && shift && exec "$@"' _ "$START_DIR" "${CMD_PARTS[@]}" -- "$PROMPT"
    fi
else
    # Interactive mode: claude TUI, real TERM, chosen CWD
    exec env -i "${ENV_VARS[@]}" TERM="${TERM:-xterm-256color}" \
        bash -c 'cd "$1" && exec claude' _ "$START_DIR"
fi
