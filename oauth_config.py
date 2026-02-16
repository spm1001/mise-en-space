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
    # --- Core: Search + Fetch ---
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/contacts.readonly',

    # --- Create (need write access) ---
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/presentations',
    'https://www.googleapis.com/auth/drive.file',  # Create files in Drive

    # --- Activity + Context (UI parity+) ---
    # Drive Activity: See who did what, when. Enables action item discovery
    # via comment events (workaround for followup:actionitems).
    'https://www.googleapis.com/auth/drive.activity.readonly',

    # Tasks: See Google Tasks. Action items from Docs/Chat can sync here.
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

# Local token storage (user's OAuth tokens, not shared)
# Absolute path so it works regardless of cwd when MCP runs
TOKEN_FILE = _PACKAGE_ROOT / 'token.json'
