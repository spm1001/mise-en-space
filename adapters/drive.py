"""
Drive adapter — Google Drive API wrapper.

Provides file metadata, export, and search. Used by fetch tool for routing.
"""

from datetime import datetime
from typing import Any, cast

from models import DriveSearchResult
from retry import with_retry
from adapters.services import get_drive_service


# Fields for file metadata — only what we need
FILE_METADATA_FIELDS = (
    "id,"
    "name,"
    "mimeType,"
    "modifiedTime,"
    "size,"
    "owners(displayName,emailAddress),"
    "webViewLink,"
    "parents"
)

# Fields for search results
SEARCH_RESULT_FIELDS = (
    "files("
    "id,"
    "name,"
    "mimeType,"
    "modifiedTime,"
    "owners(displayName),"
    "webViewLink"
    ")"
)


def _parse_datetime(dt_str: str | None) -> datetime | None:
    """Parse ISO datetime from Drive API."""
    if not dt_str:
        return None
    try:
        # Drive returns RFC 3339 format
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        return None


@with_retry(max_attempts=3, delay_ms=1000)
def get_file_metadata(file_id: str) -> dict[str, Any]:
    """
    Get file metadata for routing decisions.

    Args:
        file_id: The file ID

    Returns:
        Dict with: id, name, mimeType, modifiedTime, size, owners, webViewLink

    Raises:
        MiseError: On API failure
    """
    service = get_drive_service()

    result = (
        service.files()
        .get(fileId=file_id, fields=FILE_METADATA_FIELDS, supportsAllDrives=True)
        .execute()
    )
    return cast(dict[str, Any], result)


@with_retry(max_attempts=3, delay_ms=1000)
def export_file(file_id: str, mime_type: str) -> bytes:
    """
    Export Google Workspace file to specified format.

    For native Docs/Sheets/Slides, use this to convert to markdown/CSV/etc.
    For binary files, use download_file() instead.

    Args:
        file_id: The file ID
        mime_type: Target MIME type (e.g., 'text/markdown', 'text/csv')

    Returns:
        Exported content as bytes

    Raises:
        MiseError: On API failure
    """
    service = get_drive_service()

    # Export returns bytes directly
    result = (
        service.files()
        .export(fileId=file_id, mimeType=mime_type)
        .execute()
    )
    return cast(bytes, result)


@with_retry(max_attempts=3, delay_ms=1000)
def download_file(file_id: str) -> bytes:
    """
    Download file content (for binary/non-native files).

    Args:
        file_id: The file ID

    Returns:
        File content as bytes

    Raises:
        MiseError: On API failure
    """
    service = get_drive_service()

    # get_media returns raw bytes
    result = (
        service.files()
        .get_media(fileId=file_id)
        .execute()
    )
    return cast(bytes, result)


@with_retry(max_attempts=3, delay_ms=1000)
def search_files(
    query: str,
    max_results: int = 20,
    include_shared_drives: bool = True,
) -> list[DriveSearchResult]:
    """
    Search for files in Drive.

    Args:
        query: Drive search query (e.g., "fullText contains 'budget'")
        max_results: Maximum number of results
        include_shared_drives: Whether to search shared drives

    Returns:
        List of DriveSearchResult objects

    Raises:
        MiseError: On API failure
    """
    service = get_drive_service()

    response = (
        service.files()
        .list(
            q=query,
            pageSize=min(max_results, 100),  # API max is 100
            fields=SEARCH_RESULT_FIELDS,
            supportsAllDrives=include_shared_drives,
            includeItemsFromAllDrives=include_shared_drives,
        )
        .execute()
    )

    results: list[DriveSearchResult] = []
    for file in response.get("files", [])[:max_results]:
        owners = [
            o.get("displayName", o.get("emailAddress", ""))
            for o in file.get("owners", [])
        ]
        results.append(
            DriveSearchResult(
                file_id=file["id"],
                name=file.get("name", ""),
                mime_type=file.get("mimeType", ""),
                modified_time=_parse_datetime(file.get("modifiedTime")),
                owners=owners,
                web_view_link=file.get("webViewLink"),
            )
        )

    return results


# Common MIME types for reference
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDES_MIME = "application/vnd.google-apps.presentation"
GOOGLE_FOLDER_MIME = "application/vnd.google-apps.folder"


def is_google_workspace_file(mime_type: str) -> bool:
    """Check if MIME type is a Google Workspace native format."""
    return mime_type.startswith("application/vnd.google-apps.")
