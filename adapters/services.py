"""
Google API service initialization.

Shared by all adapters. Loads token.json, builds service objects.
"""

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build, Resource

from itv_google_auth import load_credentials
from oauth_config import TOKEN_FILE, SCOPES


def _get_credentials() -> Credentials:
    """Load OAuth credentials from token.json."""
    creds = load_credentials(TOKEN_FILE, scopes=SCOPES)
    if creds is None:
        raise FileNotFoundError(
            f"{TOKEN_FILE} not found or invalid. Run: uv run python -m auth"
        )
    return creds


# Service cache to avoid rebuilding on every call
_service_cache: dict[str, Resource] = {}


def get_sheets_service() -> Resource:
    """Get authenticated Google Sheets API service."""
    if "sheets" not in _service_cache:
        creds = _get_credentials()
        _service_cache["sheets"] = build("sheets", "v4", credentials=creds)
    return _service_cache["sheets"]


def get_drive_service() -> Resource:
    """Get authenticated Google Drive API service."""
    if "drive" not in _service_cache:
        creds = _get_credentials()
        _service_cache["drive"] = build("drive", "v3", credentials=creds)
    return _service_cache["drive"]


def get_docs_service() -> Resource:
    """Get authenticated Google Docs API service."""
    if "docs" not in _service_cache:
        creds = _get_credentials()
        _service_cache["docs"] = build("docs", "v1", credentials=creds)
    return _service_cache["docs"]


def get_gmail_service() -> Resource:
    """Get authenticated Gmail API service."""
    if "gmail" not in _service_cache:
        creds = _get_credentials()
        _service_cache["gmail"] = build("gmail", "v1", credentials=creds)
    return _service_cache["gmail"]


def get_slides_service() -> Resource:
    """Get authenticated Google Slides API service."""
    if "slides" not in _service_cache:
        creds = _get_credentials()
        _service_cache["slides"] = build("slides", "v1", credentials=creds)
    return _service_cache["slides"]


def clear_service_cache() -> None:
    """Clear cached services. Useful for testing or after re-auth."""
    _service_cache.clear()
