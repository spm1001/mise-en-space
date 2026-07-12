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
SCOPES = [
    # --- Core: Search + Fetch + Edit + Gmail Write ---
    'https://www.googleapis.com/auth/drive',  # Full access: read, write, create (superset of drive.readonly + drive.file)
    'https://www.googleapis.com/auth/gmail.modify',  # Superset of readonly: drafts, send, labels, archive
    'https://www.googleapis.com/auth/contacts.readonly',

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
    'https://www.googleapis.com/auth/calendar.readonly',

    # Forms: Read and create form structure (questions, sections, options)
    'https://www.googleapis.com/auth/forms.body',
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
    """Whether a graphical browser can be opened in THIS environment.

    Single source of truth shared by the auth.py CLI and the setup_oauth MCP
    tool, so both agree on whether to promise a browser tab or lead with the
    URL/tunnel path (mise-petaga). The subprocess setup_oauth spawns inherits
    this env, so the tool can predict the subprocess's decision exactly.
    """
    if sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


# Local credentials file (for external users who provide their own)
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
