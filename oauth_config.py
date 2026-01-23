"""
OAuth Configuration - Single Source of Truth

All OAuth parameters defined here. Do not duplicate elsewhere.
"""

from pathlib import Path

# Package root (where this file lives)
_PACKAGE_ROOT = Path(__file__).parent

# OAuth scopes for mise-en-space
# Verb model: search, fetch, create
SCOPES = [
    # Search + Fetch
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/contacts.readonly',
    # Create (need write access)
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/presentations',
    'https://www.googleapis.com/auth/drive.file',  # Create files in Drive
]

# OAuth server port (localhost callback receiver)
OAUTH_PORT = 3000

# GCP Secret Manager
GCP_PROJECT = 'mit-workspace-mcp-server'
SECRET_NAME = 'mise-credentials'

# Local token storage (user's OAuth tokens, not shared)
# Absolute path so it works regardless of cwd when MCP runs
TOKEN_FILE = _PACKAGE_ROOT / 'token.json'
