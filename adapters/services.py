"""
Google API service initialization.

Shared by all adapters. Loads token.json, builds service objects.
Uses lru_cache for thread-safe caching.

All services use a 60-second timeout to prevent indefinite hangs
when Google APIs are slow or network connections stall.
"""

from functools import lru_cache

import google_auth_httplib2
import httplib2

__all__ = [
    "get_sheets_service",
    "get_drive_service",
    "get_docs_service",
    "get_gmail_service",
    "get_slides_service",
    "build_slides_service",
    "get_activity_service",
    "get_calendar_service",
    "get_tasks_service",
    "clear_service_cache",
]

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build, Resource

from jeton import load_credentials
from oauth_config import TOKEN_FILE, SCOPES

# Default timeout for all Google API calls (seconds)
# Prevents indefinite hangs when APIs are slow or connections stall
API_TIMEOUT = 60


def _get_credentials() -> Credentials:
    """Load OAuth credentials from token.json."""
    creds = load_credentials(TOKEN_FILE, scopes=SCOPES)
    if creds is None:
        raise FileNotFoundError(
            f"{TOKEN_FILE} not found or invalid. Run: uv run python -m auth"
        )
    return creds


def _get_authorized_http(creds: Credentials) -> google_auth_httplib2.AuthorizedHttp:
    """Create authorized HTTP client with timeout."""
    http = httplib2.Http(timeout=API_TIMEOUT)
    return google_auth_httplib2.AuthorizedHttp(creds, http=http)


@lru_cache(maxsize=1)
def get_sheets_service() -> Resource:
    """Get authenticated Google Sheets API service (cached, thread-safe)."""
    creds = _get_credentials()
    return build("sheets", "v4", http=_get_authorized_http(creds))


@lru_cache(maxsize=1)
def get_drive_service() -> Resource:
    """Get authenticated Google Drive API service (cached, thread-safe)."""
    creds = _get_credentials()
    return build("drive", "v3", http=_get_authorized_http(creds))


@lru_cache(maxsize=1)
def get_docs_service() -> Resource:
    """Get authenticated Google Docs API service (cached, thread-safe)."""
    creds = _get_credentials()
    return build("docs", "v1", http=_get_authorized_http(creds))


@lru_cache(maxsize=1)
def get_gmail_service() -> Resource:
    """Get authenticated Gmail API service (cached, thread-safe)."""
    creds = _get_credentials()
    return build("gmail", "v1", http=_get_authorized_http(creds))


@lru_cache(maxsize=1)
def get_slides_service() -> Resource:
    """Get authenticated Google Slides API service (cached, thread-safe)."""
    creds = _get_credentials()
    return build("slides", "v1", http=_get_authorized_http(creds))


def build_slides_service() -> Resource:
    """Build a fresh Slides service with its own HTTP connection.

    NOT cached — each call creates a new httplib2 connection.
    Use for parallel thumbnail fetches where threads need isolated connections
    (shared httplib2 connections cause SSL corruption under concurrency).
    """
    creds = _get_credentials()
    return build("slides", "v1", http=_get_authorized_http(creds))


@lru_cache(maxsize=1)
def get_activity_service() -> Resource:
    """Get authenticated Drive Activity API v2 service (cached, thread-safe)."""
    creds = _get_credentials()
    return build("driveactivity", "v2", http=_get_authorized_http(creds))


@lru_cache(maxsize=1)
def get_calendar_service() -> Resource:
    """Get authenticated Google Calendar API v3 service (cached, thread-safe)."""
    creds = _get_credentials()
    return build("calendar", "v3", http=_get_authorized_http(creds))


@lru_cache(maxsize=1)
def get_tasks_service() -> Resource:
    """Get authenticated Google Tasks API v1 service (cached, thread-safe)."""
    creds = _get_credentials()
    return build("tasks", "v1", http=_get_authorized_http(creds))


def clear_service_cache() -> None:
    """Clear cached services. Useful for testing or after re-auth."""
    get_sheets_service.cache_clear()
    get_drive_service.cache_clear()
    get_docs_service.cache_clear()
    get_gmail_service.cache_clear()
    get_slides_service.cache_clear()
    get_activity_service.cache_clear()
    get_calendar_service.cache_clear()
    get_tasks_service.cache_clear()
