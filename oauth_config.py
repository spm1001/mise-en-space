"""
OAuth Configuration - Single Source of Truth

All OAuth parameters defined here. Do not duplicate elsewhere.
Also holds port_is_free() — the callback-port pre-check shared by the
MCP setup_oauth tool and the auth.py CLI — and the OAuth-client seam
(MISE_EN_SPACE_OAUTH_CLIENT / MISE_EN_SPACE_DATA_DIR) that lets a kit tell the engine which
Workspace it serves.
"""

import json
import os
import re
import shlex
import socket
import sys
from pathlib import Path

# Package root (where this file lives)
_PACKAGE_ROOT = Path(__file__).parent

# OAuth scopes for mise-en-space
# Goal: More effective than a human with UI access, on every dimension
#
# ADDING A SCOPE? Enable the matching API on the GCP project behind EACH
# flavour's OAuth client — there are two: the ITV one and planetmodha-workspace-mcp
# (mise-home). Enablement is per-project, so doing one and not the other fails
# only for the other flavour's users, at consent time, with
# "Error 400: access_not_configured" (a family user hit this 2026-08-15: tasks, labels
# and admin APIs were on for ITV but not planetmodha). The client_id prefix in
# that error is the owning project's number — `gcloud projects describe <project>
# --format='value(projectNumber)'` confirms which project to fix.
#
# AND the same error string has a SECOND, independent source: Google Workspace
# app access control (admin.google.com → Security → API controls) blocking an
# OAuth client — and being domain-owned does NOT exempt you: with "Trust
# internal, domain-owned apps" unticked, a client in the org's own GCP project
# is blocked like any stranger's (planetmodha had it unticked, 2026-08-15;
# ITV's is on, which is why the ITV flavour never needed an admin step). The
# check is evaluated per signed-in ACCOUNT, after login, so curl without
# cookies can never reproduce it (measured: anonymous requests 302 to sign-in
# even with a disabled API's scope). Discriminator: the base64 authError in
# the error page URL decodes to the blocking Workspace's own denial text
# (ITV's says "Tech Central"). Fix is in the blocked account's Workspace
# admin console, not in GCP.
SCOPES = [
    # --- Core: Search + Fetch + Edit + Gmail Write ---
    'https://www.googleapis.com/auth/drive',  # Full access: read, write, create (superset of drive.readonly + drive.file)
    'https://www.googleapis.com/auth/gmail.modify',  # Superset of readonly: drafts, send, labels, archive
    # NB contacts.readonly was requested here from 2026-01-23 to 2026-08-10 and
    # NEVER used by a line of code. Removed with mise-mahiho, and worth recording
    # why it mattered: it reads like directory access and is not — it covers the
    # user's OWN address book. Its presence in this list (and as "Contacts (read)"
    # in the README) made the staff directory look like solved ground for six
    # months, which is a large part of why nobody probed the real gap. An unused
    # scope is not merely dead weight; it is a false claim about capability.

    # --- Create (need write access) ---
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/presentations',

    # --- Activity + Context (UI parity+) ---
    # Drive Activity: See who did what, when. Enables action item discovery
    # via comment events (workaround for followup:actionitems).
    'https://www.googleapis.com/auth/drive.activity.readonly',

    # Tasks: Google Tasks — action items from Docs/Chat sync here.
    # Needed for action item surfacing (mise-NiKuki, mise-kecigu).
    'https://www.googleapis.com/auth/tasks.readonly',

    # Drive Labels: Organizational metadata (priority, status, etc.)
    # Enterprise feature but useful when available.
    'https://www.googleapis.com/auth/drive.labels.readonly',

    # Calendar: Meeting context (who was in the meeting, when, what docs linked)
    # Helps correlate docs with discussions.
    # calendar.events (not .readonly) since 2026-08-09: covers the same event
    # reads the adapter has always done (events-on-primary only — no
    # calendarList, no settings) PLUS the responseStatus write that RSVP needs.
    # Sameer chose the scope route over a CDP-browser workaround (mise-forunu
    # closed the zero-scope route as a measured negative). Existing tokens keep
    # working for reads; the respond op teaches re-auth on 403.
    'https://www.googleapis.com/auth/calendar.events',

    # Calendar list: WHICH calendars this account can see (mise-cegeva,
    # 2026-09-14). calendar.events reads events on any calendar you can NAME
    # but never users/me/calendarList — probed 2026-09-12: calendarList 403'd
    # while events.list on a shared family calendar answered — so a search
    # that assumed 'primary' silently missed every calendar shared into the
    # account. With this scope the default calendar search fans out over the
    # list and cues which calendars it read. Sameer chose the scope over a
    # configured calendar_ids list (Option B) on 12 and 13 Sep. ONE-OFF
    # RE-CONSENT for every mise user: a token minted before this date lacks
    # the scope, calendarList 403s, and search falls back to 'primary' with a
    # cues.calendar_scope line teaching setup_oauth(force=True) until the
    # token carries it. Every other calendar call keeps working meanwhile.
    'https://www.googleapis.com/auth/calendar.readonly',

    # Free/busy: colleagues' availability for scheduling (mise-rijeco).
    # freebusy.query does NOT accept calendar.events (probed live 2026-08-19:
    # 403 insufficient scopes on the working events token) — its accepted set
    # is calendar / calendar.readonly / calendar.freebusy / calendar.events.freebusy,
    # and this is the narrowest of them: free/busy blocks only, nothing else.
    # Tokens minted before 2026-08-19 keep working for everything except
    # do(freebusy), which teaches setup_oauth(force=True) on 403.
    'https://www.googleapis.com/auth/calendar.freebusy',

    # Forms: Read and create form structure (questions, sections, options)
    'https://www.googleapis.com/auth/forms.body',

    # Directory: colleagues' public profiles — title, department, location and
    # the reporting line — so Claude can tell who someone is (mise-mahiho).
    #
    # NOT an admin capability, despite the name. Google documents users.get and
    # users.list with viewType=domain_public as available to ANY domain user
    # ("Retrieve a user as a non-administrator"), and adapters/people.py passes
    # domain_public on every request. Measured on ITV 2026-08-10: those calls
    # return 200 on a plain user token while the same call WITHOUT
    # domain_public returns 403 "Not Authorized" — that differing error is the
    # control proving the token holds no administrator rights.
    #
    # The lighter-sounding alternative, People API directory.readonly, was
    # measured and rejected: its prefix query matches names and emails only
    # (a job title returns zero) and it has no reverse lookup.
    'https://www.googleapis.com/auth/admin.directory.user.readonly',
]

# OAuth server port (localhost callback receiver)
OAUTH_PORT = 3000


def port_is_free(port: int) -> bool:
    """Check if localhost:port is bindable. Returns True if free.

    SO_REUSEADDR matches the listener's own bind semantics (http.server sets
    allow_reuse_address) — without it, a TIME_WAIT socket from a just-finished
    flow fails this check for ~60s while the real listener would bind fine.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("localhost", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()

def can_open_browser() -> bool:
    """Whether a graphical browser is available AND suitable for OAuth here.

    Single source of truth shared by the auth.py CLI and the setup_oauth MCP
    tool, so both agree on whether to promise a browser tab or lead with the
    URL/--code path (mise-petaga). The subprocess setup_oauth spawns inherits
    this env, so the tool can predict the subprocess's decision exactly.

    Two suitability gates beyond bare availability (mise-zikesa):
    - MISE_NO_BROWSER: explicit operator override for boxes whose browser is
      signed into the wrong Google account (e.g. a remote-desktop box).
      Detection can't know account suitability; this is the honest lever.
    - XRDP_SESSION: best-effort auto-detect of an xrdp remote desktop, whose
      browser is the remote box's own — firing xdg-open at it burns the
      consent click on "access blocked" when accounts don't line up.
    """
    if os.environ.get("MISE_NO_BROWSER") or os.environ.get("XRDP_SESSION"):
        return False
    if sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


# --- Ambient (service-account) scope tiers — mise-wasagu ---
# Drive-family only: a service account has no Gmail mailbox and no personal
# calendar, so ambient mode never requests those scopes (gmail-backed ops
# refuse with a teaching error instead of leaving Google to answer an
# opaque insufficient-scope 403). The tier is fixed per DEPLOYMENT, not
# per call: Drive sharing — which folders the SA can see — is the fence
# that bounds blast radius, and MISE_SCOPES=readonly covers consumers that
# never write (decided with Sameer 2026-08-12, superseding the per-call
# sketch in mise-dehebi's original brief).
AMBIENT_SCOPES_READWRITE = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/presentations',
    'https://www.googleapis.com/auth/forms.body',
]
AMBIENT_SCOPES_READONLY = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/documents.readonly',
    'https://www.googleapis.com/auth/spreadsheets.readonly',
    'https://www.googleapis.com/auth/presentations.readonly',
    'https://www.googleapis.com/auth/forms.body.readonly',
]


def ambient_scopes() -> list[str]:
    """Scope set for ambient mode, chosen once at construction (MISE_SCOPES)."""
    tier = os.environ.get('MISE_SCOPES', '')
    if tier == 'readonly':
        return list(AMBIENT_SCOPES_READONLY)
    if tier in ('', 'readwrite'):
        return list(AMBIENT_SCOPES_READWRITE)
    raise ValueError(
        f"MISE_SCOPES={tier!r} is not recognised — 'readonly' or 'readwrite' "
        "(the default). Unset it for read-write."
    )


# --- The OAuth-client seam (mise-nujina, W4 of the estate rebuild) ----------
#
# Which OAuth client mise signs in with, and where its token lives, can be
# told to the engine from OUTSIDE, so one engine serves any Workspace and the
# client lives in each kit's wiring rather than in the engine:
#
#   MISE_EN_SPACE_OAUTH_CLIENT  path to a Google installed-app client JSON
#   MISE_EN_SPACE_DATA_DIR      absolute directory for token.json, PKCE state, setup log
#
# Both unset is the pre-seam behaviour byte for byte: the credentials.json
# bundled beside the engine, and the flavour's own data dir below (the flavour
# transform rewrites its name per build). A kit wires them in its mcpServers
# entry, e.g.
#   "env": {"MISE_EN_SPACE_OAUTH_CLIENT": "${CLAUDE_PLUGIN_ROOT}/oauth-client.json",
#           "MISE_EN_SPACE_DATA_DIR": "${CLAUDE_PLUGIN_DATA}"}
# — explicitly. The engine never reads CLAUDE_PLUGIN_DATA itself: Claude Code
# exports it to the MCP server but never to the Bash tool, so an implicit read
# would put the server's token in one store and a `--code` re-auth run from
# Bash in another. cli_env_prefix() carries both values into that command.
#
# The MISE_EN_SPACE_ prefix is deliberate: MISE_DATA_DIR belongs to jdx/mise,
# the unrelated runtime-version manager, whose docs tell users to export it —
# so the short name would have switched this seam on for anyone using that
# tool, and sent their token into its tools directory (essayeur, 2026-09-27).
CLIENT_ENV = 'MISE_EN_SPACE_OAUTH_CLIENT'
DATA_DIR_ENV = 'MISE_EN_SPACE_DATA_DIR'

# The client that ships beside the engine today. The flavour transform swaps
# this file per build; an engine that ships none is "not configured".
BUNDLED_CLIENT_FILE = _PACKAGE_ROOT / 'credentials.json'


class ClientNotConfigured(ValueError):
    """No usable OAuth client — the message says which knob to turn."""


def client_from_env() -> bool:
    """True when the OAuth client was supplied from outside (MISE_EN_SPACE_OAUTH_CLIENT)."""
    return bool(os.environ.get(CLIENT_ENV))


def read_client_id(path: Path) -> str:
    """client_id from a Google client-secrets JSON; refuses anything else."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ClientNotConfigured(
            f"OAuth client file {path} could not be read as JSON "
            f"({type(e).__name__}: {e})."
        )
    if isinstance(data, dict):
        for kind in ('installed', 'web'):
            section = data.get(kind)
            if isinstance(section, dict) and section.get('client_id'):
                return str(section['client_id'])
    raise ClientNotConfigured(
        f"OAuth client file {path} has no installed/web client_id — expected "
        "the client-secrets JSON Google Cloud Console downloads for a Desktop "
        "app client."
    )


def oauth_client_file() -> Path:
    """The OAuth client mise authenticates with, validated.

    MISE_EN_SPACE_OAUTH_CLIENT wins and is authoritative: a named file that is missing
    or malformed refuses rather than falling back to the bundled client,
    because the bundled client is a different Workspace's — falling through
    would be a silent identity switch (the same rule as MISE_TOKEN_PATH).
    """
    raw = os.environ.get(CLIENT_ENV)
    if raw:
        path = Path(raw).expanduser()
        if not path.is_file():
            raise ClientNotConfigured(
                f"{CLIENT_ENV}={raw!r} names no readable file. Fix the path — "
                "mise will not fall back to a bundled client when one is "
                "named, because that would sign in to a different Workspace."
            )
        read_client_id(path)
        return path
    if BUNDLED_CLIENT_FILE.is_file():
        read_client_id(BUNDLED_CLIENT_FILE)
        return BUNDLED_CLIENT_FILE
    raise ClientNotConfigured(
        "mise has no OAuth client configured. Set MISE_EN_SPACE_OAUTH_CLIENT to the "
        "path of a Google installed-app client JSON — the Workspace's own "
        "setup (its kit wiring) provides it. An existing token keeps working "
        "meanwhile; only signing in needs the client."
    )


def configured_client_id() -> str | None:
    """client_id of the configured client, or None when none is configured.

    A client named in MISE_EN_SPACE_OAUTH_CLIENT that fails to load raises: the
    operator asked for a specific identity, and None would read as
    'no preference'.
    """
    try:
        return read_client_id(oauth_client_file())
    except ClientNotConfigured:
        if client_from_env():
            raise
        return None


def cli_env_prefix() -> str:
    """The MISE_* assignments a shell-run `python -m auth` needs to reach the
    same client and token store as this process (empty when neither is set)."""
    parts = [
        f"{name}={shlex.quote(value)}"
        for name in (CLIENT_ENV, DATA_DIR_ENV)
        if (value := os.environ.get(name))
    ]
    return " ".join(parts) + " " if parts else ""


def cli_auth_command(args: str) -> str:
    """A pasteable `python -m auth` line: the shell has neither the server's cwd
    nor its MISE_* env, so spell both out (else "No module named auth")."""
    return f"cd {shlex.quote(str(_PACKAGE_ROOT))} && {cli_env_prefix()}uv run python -m auth {args}"


# Plugin data directory — version-stable, survives plugin cache upgrades AND
# Cowork's session-scoped staging dir wipes. Path.home() on the Mac side resolves
# to the real user home regardless of whether mise is running under Claude Code
# or Cowork, so this is always persistent across sessions.
_DEFAULT_DATA_DIR = Path.home() / '.claude' / 'plugins' / 'data' / 'mise-batterie-de-savoir'

# Where each flavour kept its token before the seam. An empty MISE_EN_SPACE_DATA_DIR
# adopts from these — by COPY, and only a token minted by the configured
# client (token_store) — so moving a Workspace onto kit wiring costs no
# re-consent, and older plugin versions still reading the old store keep
# working. The flavour transform rewrites the first name to 'mise-home' in
# the home build, where the pair then collapses to one entry: harmless, and
# the transform retires with the kit repackaging (bds-jakemi).
PRE_SEAM_DATA_DIRS = tuple(dict.fromkeys((
    _DEFAULT_DATA_DIR,
    Path.home() / '.claude' / 'plugins' / 'data' / 'mise-home',
)))


_UNFILLED = re.compile(r"\$\{?[A-Za-z_]")


def _unfilled_fallback_dir() -> Path:
    """Where the store goes when the host hands over MISE_EN_SPACE_DATA_DIR with a
    placeholder it never filled in. Keyed by the configured client's GCP project
    number (the client_id prefix), so each Workspace keeps its own stable store
    whatever the host does. With no client supplied from outside, the bundled
    client's own dir, which is where that client's token already lives."""
    if not client_from_env():
        return _DEFAULT_DATA_DIR
    client_id = configured_client_id()
    if not client_id:
        return _DEFAULT_DATA_DIR
    return Path.home() / '.claude' / 'plugins' / 'data' / f"mise-client-{client_id.split('-', 1)[0]}"


def resolve_data_dir() -> Path:
    """MISE_EN_SPACE_DATA_DIR when set (must be absolute), else the flavour's own dir.

    A placeholder the host left unfilled is a designed failure, not an operator
    error: the Claude desktop app's local server runner (Cowork) expands
    ${CLAUDE_PLUGIN_ROOT} in a plugin's server env but hands ${CLAUDE_PLUGIN_DATA}
    over literally (measured on the family kit, 28 Sep 2026), and refusing it
    left every family machine's Google tools dead there. So expand what the
    process environment can fill, and failing that use a per-client store and
    say so on stderr, which lands in the host's MCP log.
    """
    raw = os.environ.get(DATA_DIR_ENV)
    if not raw:
        return _DEFAULT_DATA_DIR
    expanded = os.path.expandvars(raw)
    if _UNFILLED.search(expanded):
        fallback = _unfilled_fallback_dir()
        print(f"mise: {DATA_DIR_ENV}={raw!r} arrived with a placeholder the host did not "
              f"fill in; using {fallback}", file=sys.stderr)
        return fallback
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        # A relative store would resolve against whatever cwd the MCP server
        # was spawned in, so the token would move between launches.
        raise ValueError(
            f"{DATA_DIR_ENV}={raw!r} must be an absolute path — a kit passes "
            "${CLAUDE_PLUGIN_DATA}."
        )
    return path


def data_dir_from_env() -> bool:
    """True when the token store was supplied from outside (MISE_EN_SPACE_DATA_DIR)."""
    return bool(os.environ.get(DATA_DIR_ENV))


DATA_DIR = resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Local token storage (user's OAuth tokens, not shared).
# Always uses the persistent data dir — the legacy fallback to _PACKAGE_ROOT
# silently lost tokens on Cowork because the staging dir is wiped per session.
TOKEN_FILE = DATA_DIR / 'token.json'
