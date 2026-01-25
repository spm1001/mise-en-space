"""
Drive adapter — Google Drive API wrapper.

Provides file metadata, export, and search. Used by fetch tool for routing.
"""

import io
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from googleapiclient.http import MediaIoBaseDownload

from models import DriveSearchResult, MiseError, ErrorKind
from retry import with_retry
from adapters.services import get_drive_service


# Size threshold for streaming — files larger than this stream to disk
# 50MB is conservative: Python handles 100MB+ fine, but OOM is catastrophic.
# Google recommends streaming for >5MB (bandwidth), but that's aggressive.
# For gigabyte PPTXs, any reasonable threshold triggers the safety net.
# Can be overridden via environment variable if needed.
import os
STREAMING_THRESHOLD_BYTES = int(os.environ.get("MISE_STREAMING_THRESHOLD_MB", 50)) * 1024 * 1024


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
    "webViewLink,"
    "contentSnippet"
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
def download_file(file_id: str, max_memory_size: int = STREAMING_THRESHOLD_BYTES) -> bytes:
    """
    Download file content (for binary/non-native files).

    For files larger than max_memory_size, use download_file_to_temp() instead
    to avoid OOM errors.

    Args:
        file_id: The file ID
        max_memory_size: Maximum size to load into memory (default: 50MB)

    Returns:
        File content as bytes

    Raises:
        MiseError: If file too large for memory, or on API failure
    """
    # Check size first
    service = get_drive_service()
    metadata = (
        service.files()
        .get(fileId=file_id, fields="size", supportsAllDrives=True)
        .execute()
    )

    file_size = int(metadata.get("size", 0))
    if file_size > max_memory_size:
        raise MiseError(
            ErrorKind.INVALID_INPUT,
            f"File too large for memory download ({file_size / (1024*1024):.1f}MB). "
            f"Use download_file_to_temp() for files over {max_memory_size / (1024*1024):.0f}MB.",
            details={"file_id": file_id, "size_bytes": file_size},
        )

    # Small file: load into memory
    result = (
        service.files()
        .get_media(fileId=file_id)
        .execute()
    )
    return cast(bytes, result)


@with_retry(max_attempts=3, delay_ms=1000)
def download_file_to_temp(file_id: str, suffix: str = "") -> Path:
    """
    Download file content to a temporary file (streaming, memory-safe).

    Use this for large files to avoid OOM. Caller is responsible for
    cleaning up the temp file when done.

    Args:
        file_id: The file ID
        suffix: File suffix (e.g., ".pdf", ".pptx")

    Returns:
        Path to temp file containing downloaded content

    Raises:
        MiseError: On API failure
    """
    service = get_drive_service()

    # Create request for streaming download
    request = service.files().get_media(fileId=file_id)

    # Create temp file
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_path = Path(tmp.name)

    try:
        # Stream download in chunks
        downloader = MediaIoBaseDownload(tmp, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        tmp.close()
        return tmp_path
    except Exception:
        # Clean up temp file on error
        tmp.close()
        tmp_path.unlink(missing_ok=True)
        raise


def get_file_size(file_id: str) -> int:
    """
    Get file size in bytes.

    Args:
        file_id: The file ID

    Returns:
        File size in bytes (0 if unknown)
    """
    service = get_drive_service()
    metadata = (
        service.files()
        .get(fileId=file_id, fields="size", supportsAllDrives=True)
        .execute()
    )
    return int(metadata.get("size", 0))


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
                snippet=file.get("contentSnippet"),
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
