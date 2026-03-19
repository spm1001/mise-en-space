"""
OAuth Configuration - Single Source of Truth

All OAuth parameters defined here. Do not duplicate elsewhere.
"""

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
]

# OAuth server port (localhost callback receiver)
OAUTH_PORT = 3000

# Local credentials file (for external users who provide their own)
LOCAL_CREDENTIALS_FILE = _PACKAGE_ROOT / 'credentials.json'

# GCP Secret Manager (optional — used by maintainer when local credentials.json absent)
GCP_PROJECT = 'mit-workspace-mcp-server'
SECRET_NAME = 'mise-credentials'

# Plugin data directory — version-stable, survives plugin cache upgrades.
# Claude Code creates ~/.claude/plugins/data/{name}-{publisher}/ automatically.
# Falls back to _PACKAGE_ROOT if the data dir doesn't exist (e.g. running from repo).
_PLUGIN_DATA_DIR = Path.home() / '.claude' / 'plugins' / 'data' / 'mise-batterie-de-savoir'

# Local token storage (user's OAuth tokens, not shared)
# Prefer plugin data dir (version-stable) over package root (version-specific)
TOKEN_FILE = (
    _PLUGIN_DATA_DIR / 'token.json'
    if _PLUGIN_DATA_DIR.is_dir()
    else _PACKAGE_ROOT / 'token.json'
)
