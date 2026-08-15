"""
OAuth Configuration - Single Source of Truth

All OAuth parameters defined here. Do not duplicate elsewhere.
Also holds port_is_free() — the callback-port pre-check shared by the
MCP setup_oauth tool and the auth.py CLI.
"""

import os
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
# "Error 400: access_not_configured" (Isaac hit this 2026-08-15: tasks, labels
# and admin APIs were on for ITV but not planetmodha). The client_id prefix in
# that error is the owning project's number — `gcloud projects describe <project>
# --format='value(projectNumber)'` confirms which project to fix.
#
# AND the same error string has a SECOND, independent source: Google Workspace
# app access control (admin.google.com → Security → API controls) blocking a
# third-party OAuth client the admin hasn't configured — evaluated per signed-in
# ACCOUNT, after login, so curl without cookies can never reproduce it (measured
# 2026-08-15: anonymous requests 302 to sign-in even with a disabled API's
# scope). Discriminator: the base64 authError in the error page URL decodes to
# the blocking Workspace's own denial text (ITV's says "Tech Central"). Fix is
# in the blocked account's Workspace admin console, not in GCP.
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


# Local credentials file (for external users who provide their own)
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


LOCAL_CREDENTIALS_FILE = _PACKAGE_ROOT / 'credentials.json'

# GCP Secret Manager (optional — used by maintainer when local credentials.json absent)
GCP_PROJECT = 'planetmodha-tools'
SECRET_NAME = 'aby-hemimi-credentials'

# Plugin data directory — version-stable, survives plugin cache upgrades AND
# Cowork's session-scoped staging dir wipes. Path.home() on the Mac side resolves
# to the real user home regardless of whether mise is running under Claude Code
# or Cowork, so this is always persistent across sessions.
_PLUGIN_DATA_DIR = Path.home() / '.claude' / 'plugins' / 'data' / 'mise-batterie-de-savoir'
_PLUGIN_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Local token storage (user's OAuth tokens, not shared).
# Always uses the persistent data dir — the legacy fallback to _PACKAGE_ROOT
# silently lost tokens on Cowork because the staging dir is wiped per session.
TOKEN_FILE = _PLUGIN_DATA_DIR / 'token.json'
