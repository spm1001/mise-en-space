"""
OAuth Configuration - Single Source of Truth

All OAuth parameters defined here. Do not duplicate elsewhere.
"""

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
TOKEN_FILE = 'token.json'
