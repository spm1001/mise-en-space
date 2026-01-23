"""
Google API service initialization.

Shared by all adapters. Loads token.json, builds service objects.
Uses lru_cache for thread-safe caching.
"""

from functools import lru_cache

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


@lru_cache(maxsize=8)
def get_sheets_service() -> Resource:
    """Get authenticated Google Sheets API service (cached, thread-safe)."""
    creds = _get_credentials()
    return build("sheets", "v4", credentials=creds)


@lru_cache(maxsize=8)
def get_drive_service() -> Resource:
    """Get authenticated Google Drive API service (cached, thread-safe)."""
    creds = _get_credentials()
    return build("drive", "v3", credentials=creds)


@lru_cache(maxsize=8)
def get_docs_service() -> Resource:
    """Get authenticated Google Docs API service (cached, thread-safe)."""
    creds = _get_credentials()
    return build("docs", "v1", credentials=creds)


@lru_cache(maxsize=8)
def get_gmail_service() -> Resource:
    """Get authenticated Gmail API service (cached, thread-safe)."""
    creds = _get_credentials()
    return build("gmail", "v1", credentials=creds)


@lru_cache(maxsize=8)
def get_slides_service() -> Resource:
    """Get authenticated Google Slides API service (cached, thread-safe)."""
    creds = _get_credentials()
    return build("slides", "v1", credentials=creds)


def clear_service_cache() -> None:
    """Clear cached services. Useful for testing or after re-auth."""
    get_sheets_service.cache_clear()
    get_drive_service.cache_clear()
    get_docs_service.cache_clear()
    get_gmail_service.cache_clear()
    get_slides_service.cache_clear()
