#!/bin/bash
# SessionStart hook: ensure the mise MCP server can start, and install this
# flavour's rules shard. Silent when all is well; helpful and FLAVOUR-AWARE when
# it's not (mise-tatego). One mise engine serves two Workspaces — ITV through
# the batterie kit, Planet Modha through the family kit (since the 2.0.0 fold;
# before it, the mise and mise-home plugins) — and this hook is what lets a
# Claude tell them apart, instead of reading one's missing-token warning as
# "the other mise is broken". The family kit wires its own hook, not this one.
#
# THE SHARD IS REWRITTEN FROM HERE EVERY SESSION START (temp+mv, below). Editing
# ~/.claude/rules/mise*.md by hand is therefore a no-op that survives until the
# next session and no further — fix the shard HERE, or in instructions.md.

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_PLUGIN_ROOT="$(dirname "$HOOK_DIR")"
PLUGIN_JSON="$_PLUGIN_ROOT/.claude-plugin/plugin.json"

# --- Identity: read this flavour's stamped fields ONCE -----------------------
# Used by the NO-TOKEN WARNING below, which must name which flavour is unauthed
# and which sibling is fine. The rules shard no longer speaks in identity at all
# — it carries a static routing rule (see there for why). base mise ships
# identity "ITV (itv.com)" in its source plugin.json; make-mise-flavour.sh
# overwrites it to "Planet Modha (planetmodha)" for mise-home. Older installs
# lacking the field degrade to name-only wording (IDENTITY empty → the
# identity/sibling clauses of the warning are omitted).
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
  *itv*) SIBLING_DISPLAY="the family kit's mise"; SIBLING_IDENTITY="Planet Modha (planetmodha)" ;;
  *)     SIBLING_DISPLAY="Mise";      SIBLING_IDENTITY="ITV (itv.com)" ;;
esac

# --- Install the rules shard, stamped with this flavour's identity (betiko) --
if [ -f "$_PLUGIN_ROOT/instructions.md" ]; then
    mkdir -p "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/rules"
    # Filename derives from the plugin name (mise.md / mise-home.md), so the hook
    # self-adjusts per flavour and needs no rules/<name>.md substitution in the
    # transform. Copy, NOT symlink: the plugin root can be an ephemeral temp dir
    # (Desktop stages under /var/folders, which macOS purges) — a symlink there
    # dangles and the shard vanishes. Re-run each session-start keeps it current.
    # Do NOT revert to ln -sf.
    RULES_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/rules"
    RULES_DEST="$RULES_DIR/${NAME}.md"
    # ONE SHARD WHEN BOTH FLAVOURS ARE INSTALLED (carte-kelori, 2026-09-27). The
    # text below is byte-identical in both builds bar the stamp line, and every
    # rules/*.md loads, so two flavours put the same ~1.8k into every session
    # twice. The work flavour (`mise`) is the designated writer; any other
    # flavour stands down, and removes its own copy, when `mise` is registered
    # AND its shard is on disk. Either missing (mise uninstalled, or its hook
    # not yet run on a fresh machine), this flavour writes its own as before, so
    # the text is never absent — the worst case is one session carrying both.
    # Since the kit fold (bds-jakemi) the work flavour is registered as
    # batterie@batterie, so either key counts; the shard-on-disk test beside
    # it keeps a pre-fold batterie without mise from counting.
    # The literal "mise" below survives the flavour transform, which rewrites
    # *.md/*.py only (see the routing-rule note further down).
    _REG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/installed_plugins.json"
    if [ "$NAME" != "mise" ] && [ -f "$RULES_DIR/mise.md" ] \
       && grep -qE '"mise@|"batterie@batterie"' "$_REG" 2>/dev/null; then
        rm -f "$RULES_DEST"
    else
    # Robust write via temp+mv: a stale entry may be a SYMLINK from an older
    # session, and cp-ing source over a symlink-to-source errors ("same file").
    # mv -f replaces the entry atomically whatever it was, never following it.
    _tmp="$(mktemp "${RULES_DEST}.XXXXXX")"
    {
        # A ROUTING RULE, not an identity claim. Every rules/*.md loads
        # unconditionally, so when both flavours are installed BOTH shards load
        # — and a first-person "You are **Mise** … your sibling **Mise Home**"
        # then told one session it was two different things at once. State the
        # mapping in the third person and let the reader route by it.
        #
        # STATIC text, deliberately: there are only ever two flavours (see
        # IDENTITY_BY_INSTANCE in make-mise-flavour.sh), so both halves are
        # byte-identical in both builds — nothing to derive per flavour and no
        # substitution rule for the transform to keep in step. The "ITV (itv.com)"
        # literal is why the transform's identity guard is a FIELD check rather
        # than a file-wide scan.
        #
        # The bare `mcp__mise__` below is a deliberate example of the form a
        # PLUGIN session does NOT have, so it must survive the transform
        # unrewritten — it does, because that rewrite scopes to *.md/*.py and
        # this is a .sh. Do not "fix" it to mcp__mise-home__ for the home build:
        # the sentence is about install method, not about which flavour.
        # Only the stamp below is per-flavour — it says which file this is.
        printf '<!-- mise flavour: %s -->\n' "$DISPLAY_NAME"
        printf '**Which Mise to reach for.** One mise per Google Workspace: the **batterie** kit'"'"'s '
        printf 'mise acts on **ITV (itv.com)**, the **family** kit'"'"'s on **Planet Modha (planetmodha)**. '
        printf 'Reach for whichever matches where the content lives — only the kits whose '
        printf 'tools are present in this session are installed.\n\n'
        printf '**Matching the tool names.** Both servers are called `mise`, so the KIT part of the '
        printf 'name is what tells them apart: `mcp__plugin_batterie_mise__` is ITV and '
        printf '`mcp__plugin_family_mise__` is Planet Modha (so `…__search`, `…__fetch`, `…__do`). '
        printf 'Wired as a bare MCP server instead, the `plugin_<kit>_` part is absent — a grep for '
        printf '`mcp__mise__` returns zero in a plugin session, and that zero is the harness naming '
        printf 'scheme, not a missing mise. (Before the 2.0.0 fold the Planet Modha tools were '
        printf '`mcp__plugin_mise-home_mise-home__`; a machine not yet migrated may still show them.)\n\n'
        cat "$_PLUGIN_ROOT/instructions.md"
    } > "$_tmp"
    mv -f "$_tmp" "$RULES_DEST"
    fi
fi

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
[ -z "$PLUGIN_ROOT" ] && exit 0

ISSUES=""
BLOCKING=false

# Capture uv sync output so a failed dependency install is diagnosable (bon-dotupu).
SYNC_LOG="$HOME/.cache/mise/ensure.log"
mkdir -p "$(dirname "$SYNC_LOG")" 2>/dev/null

# 1. Find uv. `command -v` respects PATH, but hook shells can run with a PATH
#    that omits uv's real home (seen live 2026-07-19: Cowork VM had uv at
#    /usr/local/bin yet the hook warned "uv not found" — mise-cuveza). Fall
#    back to the known install locations, and carry the discovered ABSOLUTE
#    path into the auto-sync below — a PATH that fails the check would have
#    failed the sync identically.
UV_BIN="$(command -v uv 2>/dev/null)"
if [ -z "$UV_BIN" ]; then
    for _c in /usr/local/bin/uv "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/bin/uv; do
        if [ -x "$_c" ]; then UV_BIN="$_c"; break; fi
    done
fi
if [ -z "$UV_BIN" ]; then
    ISSUES="${ISSUES}• uv not found — install from https://docs.astral.sh/uv/\n"
    BLOCKING=true
fi

# 2. Dependencies: a .venv holding the extraction extra, matching the lock. It
#    is what this kit's server line runs with (`uv run --extra extraction`) and
#    what ring kits' launchers exec (step 2c). Syncing WITH the extra here means
#    the server's own spawn finds the env complete, instead of adding packages
#    to it while another kit's server is starting from it. `uv sync` is exact —
#    it removes whatever the command does not name — so it always names the
#    extra. Completeness is uv's own verdict (`--check`, ~40 ms), not the
#    presence of one package's folder: a sync killed part-way once left
#    markitdown/ in place with urllib3 missing, and a ring kit exec'ing that env
#    dies at start (essayeur, 28 Sep). Where `--check` is unknown (an older uv)
#    the fallback is a plain sync, a no-op when the env is already complete.
ENGINE_SYNCED=false
if [ -n "$UV_BIN" ]; then
    if "$UV_BIN" sync --project "$PLUGIN_ROOT" --extra extraction --frozen --check --quiet >/dev/null 2>&1; then
        ENGINE_SYNCED=true
    elif "$UV_BIN" sync --project "$PLUGIN_ROOT" --extra extraction --frozen --quiet >"$SYNC_LOG" 2>&1; then
        ENGINE_SYNCED=true
    elif [ ! -d "$PLUGIN_ROOT/.venv" ]; then
        ISSUES="${ISSUES}• Dependencies not installed (full error: ${SYNC_LOG}). Run: uv sync --project \"$PLUGIN_ROOT\" --extra extraction\n"
        BLOCKING=true
    else
        ISSUES="${ISSUES}• Dependencies did not finish syncing (full error: ${SYNC_LOG}). This kit's server repairs its env when it starts; kits that run the engine by pointer (mit, family) wait for a clean sync. Run: uv sync --project \"$PLUGIN_ROOT\" --extra extraction\n"
        # advisory — `uv run` at this kit's server spawn repairs the env
    fi
else
    ISSUES="${ISSUES}• Dependencies not installed (need uv first)\n"
    BLOCKING=true
fi

# 2c. Say where this kit's engine lives, for ring kits (bds-sovabu). A ring kit
#     (mit@mit, and family once it depends on batterie) ships no engine of its
#     own: its server line runs a small launcher that reads this pointer and
#     execs <root>/.venv/bin/python <root>/server.py with the kit's own OAuth
#     client. So the engine every kit runs is this locked, CI-tested env — not a
#     separately resolved one — and nothing is ever replaced in place: each
#     plugin version has its own dir, and moving the pointer is one rename.
#     Written only after a clean sync (above). It moves forward only while the
#     recorded target is intact, so a long-lived session on an older version
#     cannot pull ring kits back onto it; the check and the rename are two
#     steps, so a same-instant race can land one version low, which the next
#     session corrects. Versions compare release part first, and a pre-release
#     (2.1.0-canary.1) ranks below its release. Plain file, not a symlink (BSD
#     mv follows a symlink to a directory); no python3 and no `sort -V`.
_ver_ge() { # is $1 >= $2 ?
    _a="${1%%-*}"; _b="${2%%-*}"
    if [ "$_a" != "$_b" ]; then
        [ "$(printf '%s\n%s\n' "$_a" "$_b" | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)" = "$_a" ]
        return
    fi
    case "$1" in
        *-*) case "$2" in *-*) [ "$(printf '%s\n%s\n' "$1" "$2" | sort | tail -1)" = "$1" ] ;; *) return 1 ;; esac ;;
        *)   return 0 ;;
    esac
}
if [ "$ENGINE_SYNCED" = true ] && [ -x "$PLUGIN_ROOT/.venv/bin/python" ] && [ -f "$PLUGIN_ROOT/server.py" ]; then
    _ptr_dir="${XDG_DATA_HOME:-$HOME/.local/share}/mise-en-space"
    _ptr="$_ptr_dir/engine"
    _my_ver=""
    for _pjf in "$PLUGIN_ROOT/../.claude-plugin/plugin.json" "$PLUGIN_ROOT/.claude-plugin/plugin.json"; do
        [ -f "$_pjf" ] || continue
        _my_ver="$(sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*/\1/p' "$_pjf" | head -1)"
        [ -n "$_my_ver" ] && break
    done
    _rec_ver=""; _rec_root=""
    if [ -f "$_ptr" ]; then
        _rec_ver="$(sed -n 's/^version=//p' "$_ptr")"
        _rec_root="$(sed -n 's/^root=//p' "$_ptr")"
    fi
    _write=false
    if [ -z "$_rec_root" ] || [ ! -x "$_rec_root/.venv/bin/python" ] || [ ! -f "$_rec_root/server.py" ]; then
        _write=true
    elif [ "$_rec_root" != "$PLUGIN_ROOT" ] && [ -n "$_my_ver" ] && _ver_ge "$_my_ver" "$_rec_ver"; then
        _write=true
    fi
    if [ "$_write" = true ]; then
        if ! { mkdir -p "$_ptr_dir" && _tmp="$(mktemp "$_ptr_dir/.engine.XXXXXX")" \
               && printf 'version=%s\nroot=%s\n' "$_my_ver" "$PLUGIN_ROOT" > "$_tmp" \
               && mv -f "$_tmp" "$_ptr"; } 2>/dev/null; then
            ISSUES="${ISSUES}• Could not record where the mise engine lives ($_ptr). This kit's own server is unaffected; a ring kit's mise (mit, family) will say it cannot find the engine.\n"
        fi
    fi
fi

# 3. Check for OAuth token (Keychain on macOS, data dir, or plugin root).
#    CHECKED names only the stores that actually exist on THIS platform — on
#    Linux there is no Keychain, so the old "checked Keychain and token.json"
#    claim was a lie that read as a defect (mise-tatego; coordinates with the
#    token_store/setup_oauth honesty in mise-petaga).
#    Quiet until configured (mise-nujina): an engine that ships no OAuth
#    client and was handed none (MISE_EN_SPACE_OAUTH_CLIENT) has not been switched on
#    for any Workspace, so a missing token is not news — say nothing. The
#    kit that supplies the client owns that nag.
UNCONFIGURED=false
if [ ! -f "$_PLUGIN_ROOT/credentials.json" ] && [ -z "${MISE_EN_SPACE_OAUTH_CLIENT:-}" ]; then
    UNCONFIGURED=true
fi
HAS_TOKEN=false
CHECKED="the token file"
if command -v security &>/dev/null; then
    CHECKED="Keychain and the token file"
    security find-generic-password -s "mise-oauth-token" -w &>/dev/null && HAS_TOKEN=true
fi
# Plugin data dir (version-stable, where token_store.py actually writes on Linux)
# — or MISE_EN_SPACE_DATA_DIR when the session carries the seam's store (oauth_config).
# The flavour's own dir name is kept separately: it is what the sibling check
# below must skip, whatever store the seam points at.
OWN_DEFAULT_DATA_DIR="$HOME/.claude/plugins/data/mise-batterie-de-savoir"
PLUGIN_DATA_DIR="${MISE_EN_SPACE_DATA_DIR:-$OWN_DEFAULT_DATA_DIR}"
if [ "$HAS_TOKEN" = false ] && [ -f "$PLUGIN_DATA_DIR/token.json" ]; then
    HAS_TOKEN=true
fi
# An empty seam store is not "no token" while the flavour's own pre-seam store
# holds one: the engine adopts from there on first load (token_store). Only
# when the client is the bundled one — adoption matches on client_id, so with
# an outside client the old token may belong to another Workspace (essayeur).
if [ "$HAS_TOKEN" = false ] && [ -z "${MISE_EN_SPACE_OAUTH_CLIENT:-}" ] \
   && [ -f "$OWN_DEFAULT_DATA_DIR/token.json" ]; then
    HAS_TOKEN=true
fi
# Legacy: plugin root (versioned cache dir)
if [ "$HAS_TOKEN" = false ] && [ -f "$PLUGIN_ROOT/token.json" ]; then
    HAS_TOKEN=true
fi

if [ "$HAS_TOKEN" = false ] && [ "$UNCONFIGURED" = false ]; then
    # Is the OTHER flavour authed? If so, a missing token HERE is ADVISORY — the
    # user has a working mise, nothing is broken (the exact 2026-07-12 misread).
    OWN_DATA="$(basename "$OWN_DEFAULT_DATA_DIR")"
    SIBLING_AUTHED=false
    # data/mise*/ are the pre-fold flavours' stores; data/family-family/ is the
    # family kit's (its mise runs on ${CLAUDE_PLUGIN_DATA}) — missed until
    # 2026-09-27, so a family member who added the batterie kit got a BLOCKING
    # "no token" nag for an ITV Workspace they have no use for.
    for _t in "$HOME"/.claude/plugins/data/mise*/token.json "$HOME"/.claude/plugins/data/family-family/token.json; do
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

# 4. Poppler (pdftotext/pdftoppm) backs PDF text extraction and page/deck
#    thumbnails (mise-mitoki, mise-releko). Its absence is silent per-fetch —
#    text degrades to markitdown, thumbnails skip — and it bites hardest on
#    exactly the dense visual decks where fidelity matters most, so say it
#    ONCE, up front. Advisory only: never block on it. Hook shells can run
#    with a truncated PATH (same trap as uv above), so probe the known
#    install homes too before concluding absence.
POPPLER_BIN="$(command -v pdftotext 2>/dev/null)"
if [ -z "$POPPLER_BIN" ]; then
    for _c in /opt/homebrew/bin/pdftotext /usr/local/bin/pdftotext /usr/bin/pdftotext; do
        if [ -x "$_c" ]; then POPPLER_BIN="$_c"; break; fi
    done
fi
if [ -z "$POPPLER_BIN" ]; then
    ISSUES="${ISSUES}• poppler is not installed — PDF text extraction degrades (tables, page markers) and PDF/deck thumbnails are skipped. Install: apt-get install poppler-utils (Debian/Ubuntu) or brew install poppler (macOS).\n"
    # advisory — do NOT set BLOCKING
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
    FOOTER="Advisory only: nothing is broken — these just make mise better."
fi
MSG="${HEADER}\n\n${ISSUES}\n${FOOTER}"

# Render via json.dumps so messages containing quotes (e.g. the quoted PLUGIN_ROOT
# in recovery commands) produce valid JSON — a raw heredoc does not escape them
# (bon-dotupu).
python3 -c "import json; print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': '''${MSG}'''}}))"
