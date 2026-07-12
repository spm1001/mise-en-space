#!/bin/bash
# SessionStart hook: ensure the mise MCP server can start, and install this
# flavour's identity-stamped rules shard. Silent when all is well; helpful and
# FLAVOUR-AWARE when it's not (mise-tatego). mise ships as two flavours from one
# source — mise (work, ITV) and mise-home (personal, Planet Modha) — and this
# hook is what lets a Claude tell them apart, instead of reading one flavour's
# missing-token warning as "the other mise is broken".

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_PLUGIN_ROOT="$(dirname "$HOOK_DIR")"
PLUGIN_JSON="$_PLUGIN_ROOT/.claude-plugin/plugin.json"

# --- Identity: read this flavour's stamped fields ONCE -----------------------
# Both surfaces below (the rules-shard header and the no-token warning) speak
# the flavour's identity. base mise ships identity "ITV (itv.com)" in its source
# plugin.json; make-mise-flavour.sh overwrites it to "Planet Modha (planetmodha)"
# for mise-home. Older installs lacking the field degrade to name-only wording
# (IDENTITY empty → the identity/sibling clauses are omitted).
_pj() { python3 -c "import json; print(json.load(open('$PLUGIN_JSON')).get('$1','') or '')" 2>/dev/null; }
NAME="$(_pj name)";                NAME="${NAME:-mise}"
DISPLAY_NAME="$(_pj displayName)"; DISPLAY_NAME="${DISPLAY_NAME:-$NAME}"
IDENTITY="$(_pj identity)"

# The sibling is the OTHER of the two (and only two) flavours, DERIVED from our
# own identity — so this block is byte-identical in both flavours (the transform
# rewrites no string here) and needs no substitution rule. Each flavour
# legitimately names the other, so both labels appear in both builds; the
# transform's identity guard is a FIELD check (not a scan) precisely so the
# "ITV (itv.com)" literal here is not a false "ITV leaked" positive.
_id_lc="$(printf '%s' "$IDENTITY" | tr '[:upper:]' '[:lower:]')"
case "$_id_lc" in
  *itv*) SIBLING_DISPLAY="Mise Home"; SIBLING_IDENTITY="Planet Modha (planetmodha)" ;;
  *)     SIBLING_DISPLAY="Mise";      SIBLING_IDENTITY="ITV (itv.com)" ;;
esac

# --- Install the rules shard, stamped with this flavour's identity (betiko) --
if [ -f "$_PLUGIN_ROOT/instructions.md" ]; then
    mkdir -p "$HOME/.claude/rules"
    # Filename derives from the plugin name (mise.md / mise-home.md), so the hook
    # self-adjusts per flavour and needs no rules/<name>.md substitution in the
    # transform. Copy, NOT symlink: the plugin root can be an ephemeral temp dir
    # (Desktop stages under /var/folders, which macOS purges) — a symlink there
    # dangles and the shard vanishes. Re-run each session-start keeps it current.
    # Do NOT revert to ln -sf.
    RULES_DEST="$HOME/.claude/rules/${NAME}.md"
    # Robust write via temp+mv: a stale entry may be a SYMLINK from an older
    # session, and cp-ing source over a symlink-to-source errors ("same file").
    # mv -f replaces the entry atomically whatever it was, never following it.
    _tmp="$(mktemp "${RULES_DEST}.XXXXXX")"
    {
        if [ -n "$IDENTITY" ]; then
            printf '<!-- mise flavour: %s -->\n' "$DISPLAY_NAME"
            printf 'You are **%s** — you act on the **%s** Google Workspace. ' "$DISPLAY_NAME" "$IDENTITY"
            printf 'Your sibling **%s** acts on **%s**. ' "$SIBLING_DISPLAY" "$SIBLING_IDENTITY"
            printf 'Reach for whichever matches the Workspace the content lives in.\n\n'
        fi
        cat "$_PLUGIN_ROOT/instructions.md"
    } > "$_tmp"
    mv -f "$_tmp" "$RULES_DEST"
fi

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
[ -z "$PLUGIN_ROOT" ] && exit 0

ISSUES=""
BLOCKING=false

# Capture uv sync output so a failed dependency install is diagnosable (bon-dotupu).
SYNC_LOG="$HOME/.cache/mise/ensure.log"
mkdir -p "$(dirname "$SYNC_LOG")" 2>/dev/null

# 1. Check uv is available
if ! command -v uv &>/dev/null; then
    ISSUES="${ISSUES}• uv not found — install from https://docs.astral.sh/uv/\n"
    BLOCKING=true
fi

# 2. Check dependencies are synced (look for .venv in plugin root)
if [ ! -d "$PLUGIN_ROOT/.venv" ]; then
    if command -v uv &>/dev/null; then
        # Auto-sync — this is safe and idempotent
        uv sync --project "$PLUGIN_ROOT" --quiet >"$SYNC_LOG" 2>&1
        if [ ! -d "$PLUGIN_ROOT/.venv" ]; then
            ISSUES="${ISSUES}• Dependencies not installed (full error: ${SYNC_LOG}). Run: uv sync --project \"$PLUGIN_ROOT\"\n"
            BLOCKING=true
        fi
    else
        ISSUES="${ISSUES}• Dependencies not installed (need uv first)\n"
        BLOCKING=true
    fi
fi

# 3. Check for OAuth token (Keychain on macOS, data dir, or plugin root).
#    CHECKED names only the stores that actually exist on THIS platform — on
#    Linux there is no Keychain, so the old "checked Keychain and token.json"
#    claim was a lie that read as a defect (mise-tatego; coordinates with the
#    token_store/setup_oauth honesty in mise-petaga).
HAS_TOKEN=false
CHECKED="the token file"
if command -v security &>/dev/null; then
    CHECKED="Keychain and the token file"
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
    # Is the OTHER flavour authed? If so, a missing token HERE is ADVISORY — the
    # user has a working mise, nothing is broken (the exact 2026-07-12 misread).
    OWN_DATA="$(basename "$PLUGIN_DATA_DIR")"
    SIBLING_AUTHED=false
    for _t in "$HOME"/.claude/plugins/data/mise*/token.json; do
        [ -e "$_t" ] || continue
        case "$_t" in */"$OWN_DATA"/*) continue ;; esac
        SIBLING_AUTHED=true
    done

    _self="$DISPLAY_NAME"
    [ -n "$IDENTITY" ] && _self="$DISPLAY_NAME, the $IDENTITY Workspace"
    _fix="ask Claude to call ${NAME}.do(operation=\"setup_oauth\") — opens a browser (or gives you a URL to click on a headless box) and saves the token. CLI alternative: cd \"$PLUGIN_ROOT\" && uv run python -m auth --auto"

    if [ "$SIBLING_AUTHED" = true ]; then
        _sib=""
        [ -n "$IDENTITY" ] && _sib=" Your other flavour ${SIBLING_DISPLAY} — which acts on ${SIBLING_IDENTITY} — is authenticated and unaffected; this is about ${DISPLAY_NAME}, not that one."
        ISSUES="${ISSUES}• ${_self} has no Google OAuth token yet (checked ${CHECKED}).${_sib} Only needed if you want to act on ${IDENTITY:-this Workspace} — to authenticate it, ${_fix}\n"
        # advisory — do NOT set BLOCKING
    else
        ISSUES="${ISSUES}• ${_self} has no Google OAuth token (checked ${CHECKED}). Easiest fix: ${_fix}\n"
        BLOCKING=true
    fi
fi

# If no issues, exit silently
[ -z "$ISSUES" ] && exit 0

# Header + footer match severity: a real blocker says "won't work"; an advisory
# (only this flavour unauthed while a sibling works) says so plainly instead.
if [ "$BLOCKING" = true ]; then
    HEADER="⚠️ ${DISPLAY_NAME} MCP server needs setup:"
    FOOTER="The MCP server won't work until these are resolved."
else
    HEADER="ℹ️ ${DISPLAY_NAME} — optional setup:"
    FOOTER="Advisory only: your other mise flavour is working, so nothing is broken."
fi
MSG="${HEADER}\n\n${ISSUES}\n${FOOTER}"

# Render via json.dumps so messages containing quotes (e.g. the quoted PLUGIN_ROOT
# in recovery commands) produce valid JSON — a raw heredoc does not escape them
# (bon-dotupu).
python3 -c "import json; print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': '''${MSG}'''}}))"
