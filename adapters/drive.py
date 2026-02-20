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

import re

from models import (
    DriveSearchResult,
    EmailContext,
    MiseError,
    ErrorKind,
    FileCommentsData,
    CommentData,
    CommentReply,
)
from retry import with_retry
from adapters.services import get_drive_service


def parse_email_context(description: str | None) -> EmailContext | None:
    """
    Parse email context from exfil'd file description.

    The Apps Script exfil stores metadata in this format:
        From: alice@example.com
        Subject: Budget analysis
        Date: 2026-01-15T10:30:00Z
        Message ID: 18f4a5b6c7d8e9f0
        Content Hash: abc123...

    Returns EmailContext if Message ID is found, None otherwise.
    """
    if not description:
        return None

    # Look for Message ID - the key identifier
    message_id_match = re.search(r"Message ID:\s*(\w+)", description)
    if not message_id_match:
        return None

    message_id = message_id_match.group(1)

    # Extract optional fields
    from_match = re.search(r"From:\s*(.+)", description)
    subject_match = re.search(r"Subject:\s*(.+)", description)
    date_match = re.search(r"Date:\s*(.+)", description)

    return EmailContext(
        message_id=message_id,
        from_address=from_match.group(1).strip() if from_match else None,
        subject=subject_match.group(1).strip() if subject_match else None,
        date=date_match.group(1).strip() if date_match else None,
    )


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
    "parents,"
    "description"  # Contains Message ID for exfil'd email attachments
)

# Fields for search results
# Note: Drive API v3 doesn't support contentSnippet - fullText search returns
# matching files but not why they matched. Snippet will be None.
SEARCH_RESULT_FIELDS = (
    "files("
    "id,"
    "name,"
    "mimeType,"
    "modifiedTime,"
    "owners(displayName),"
    "webViewLink,"
    "description"  # Contains Message ID for exfil'd email attachments
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


_DRIVE_ID_RE = re.compile(r'^[A-Za-z0-9_\-]+$')


def _validate_drive_id(drive_id: str, param_name: str = "folder_id") -> None:
    """Raise MiseError if drive_id contains characters outside the Drive ID alphabet."""
    if not _DRIVE_ID_RE.match(drive_id):
        raise MiseError(
            ErrorKind.INVALID_INPUT,
            f"Invalid {param_name}: must contain only alphanumeric characters, hyphens, and underscores",
            details={param_name: drive_id},
        )


@with_retry(max_attempts=3, delay_ms=1000)
def search_files(
    query: str,
    max_results: int = 20,
    include_shared_drives: bool = True,
    folder_id: str | None = None,
) -> list[DriveSearchResult]:
    """
    Search for files in Drive.

    Args:
        query: Drive search query (e.g., "fullText contains 'budget'")
        max_results: Maximum number of results
        include_shared_drives: Whether to search shared drives
        folder_id: Optional folder ID to scope results to immediate children only.
            Non-recursive — only files directly inside this folder are returned.

    Returns:
        List of DriveSearchResult objects

    Raises:
        MiseError: On API failure or invalid folder_id
    """
    if folder_id is not None:
        _validate_drive_id(folder_id, "folder_id")
        query = f"{query} AND '{folder_id}' in parents"

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
        description = file.get("description")
        email_context = parse_email_context(description)

        results.append(
            DriveSearchResult(
                file_id=file["id"],
                name=file.get("name", ""),
                mime_type=file.get("mimeType", ""),
                modified_time=_parse_datetime(file.get("modifiedTime")),
                snippet=file.get("contentSnippet"),
                owners=owners,
                web_view_link=file.get("webViewLink"),
                description=description,
                email_context=email_context,
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


# Fields for folder listing — name, ID, and MIME type is all we need
FOLDER_LIST_FIELDS = "nextPageToken,files(id,name,mimeType)"

# Max pages to fetch eagerly (3 pages × 100 items = 300 items)
FOLDER_LIST_MAX_PAGES = 3
FOLDER_LIST_PAGE_SIZE = 100


@with_retry(max_attempts=3, delay_ms=1000)
def list_folder(folder_id: str) -> dict:
    """
    List direct children of a Drive folder.

    Fetches up to 3 pages (300 items) ordered by name. Does not recurse.

    CRITICAL: Both supportsAllDrives=True AND includeItemsFromAllDrives=True
    are required for shared drives — omitting either returns 0 results with no error.

    Args:
        folder_id: The folder's Drive file ID

    Returns:
        Dict with:
            subfolders: list of {id, name} for child folders
            files: list of {id, name, mimeType} for child files
            file_count: int
            folder_count: int
            item_count: int (total items seen, may be < actual if truncated)
            types: list of distinct mimeType strings for files
            truncated: bool (True if nextPageToken remained after page 3)

    Raises:
        MiseError: On API failure
    """
    service = get_drive_service()

    query = f"'{folder_id}' in parents and trashed = false"

    subfolders: list[dict] = []
    files: list[dict] = []
    page_token = None
    pages_fetched = 0
    truncated = False

    while pages_fetched < FOLDER_LIST_MAX_PAGES:
        kwargs: dict = dict(
            q=query,
            pageSize=FOLDER_LIST_PAGE_SIZE,
            fields=FOLDER_LIST_FIELDS,
            orderBy="name",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        if page_token:
            kwargs["pageToken"] = page_token

        response = service.files().list(**kwargs).execute()
        pages_fetched += 1

        for item in response.get("files", []):
            if item.get("mimeType") == GOOGLE_FOLDER_MIME:
                subfolders.append({"id": item["id"], "name": item.get("name", "")})
            else:
                files.append({
                    "id": item["id"],
                    "name": item.get("name", ""),
                    "mimeType": item.get("mimeType", ""),
                })

        page_token = response.get("nextPageToken")
        if not page_token:
            break
    else:
        # Exited because pages_fetched == FOLDER_LIST_MAX_PAGES
        truncated = bool(page_token)

    types = sorted({f["mimeType"] for f in files if f["mimeType"]})

    return {
        "subfolders": subfolders,
        "files": files,
        "file_count": len(files),
        "folder_count": len(subfolders),
        "item_count": len(subfolders) + len(files),
        "types": types,
        "truncated": truncated,
    }


# =============================================================================
# PRE-EXFIL LOOKUP
# =============================================================================

from functools import lru_cache


_email_attachments_folder_id: str | None = None
_email_attachments_folder_checked: bool = False


def _get_email_attachments_folder_id() -> str | None:
    """
    Get the Email Attachments folder ID.

    Checks in order:
    1. MISE_EMAIL_ATTACHMENTS_FOLDER_ID env var
    2. Auto-discover folder named "Email Attachments" in Drive

    Returns None if not configured and can't auto-discover.
    Cached manually (not lru_cache) so None results aren't cached permanently —
    allows re-discovery if the folder is created after server start.
    """
    global _email_attachments_folder_id, _email_attachments_folder_checked

    if _email_attachments_folder_checked:
        return _email_attachments_folder_id

    # Check env var first
    folder_id = os.environ.get("MISE_EMAIL_ATTACHMENTS_FOLDER_ID")
    if folder_id:
        _email_attachments_folder_id = folder_id
        _email_attachments_folder_checked = True
        return folder_id

    # Auto-discover by name
    try:
        service = get_drive_service()
        response = (
            service.files()
            .list(
                q="name = 'Email Attachments' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                pageSize=1,
                fields="files(id)",
            )
            .execute()
        )
        files = response.get("files", [])
        if files:
            discovered_id: str = files[0]["id"]
            _email_attachments_folder_id = discovered_id
            _email_attachments_folder_checked = True
            return discovered_id
    except Exception:
        pass  # Don't cache failures — retry next call

    return None


@with_retry(max_attempts=3, delay_ms=1000)
def lookup_exfiltrated(message_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """
    Look up which messages have attachments pre-exfiltrated to Drive.

    The Apps Script exfiltrator stores metadata in file descriptions:
        Message ID: 19abc123...
        From: alice@example.com
        Subject: Budget analysis

    This queries the Email Attachments folder for files matching these
    message IDs. Found files are already indexed by Drive and can be
    fetched directly (faster than Gmail API download + extraction).

    Args:
        message_ids: List of Gmail message IDs to look up

    Returns:
        Dict mapping message_id to list of Drive file metadata:
        {
            'msg123': [
                {'file_id': '1ABC...', 'name': 'report.pdf', 'mimeType': 'application/pdf'},
                ...
            ],
            ...
        }
        Messages with no exfiltrated attachments won't have a key.
    """
    if not message_ids:
        return {}

    folder_id = _get_email_attachments_folder_id()
    if not folder_id:
        return {}

    service = get_drive_service()
    result: dict[str, list[dict[str, Any]]] = {}

    # Query for all message IDs at once using fullText search
    # Message IDs are unique (Gmail's hex format)
    # Files are in dated subfolders, so search globally (not just direct children)
    # Escape message IDs to prevent query injection via crafted descriptions
    from validation import escape_drive_query
    if len(message_ids) == 1:
        query = f"fullText contains 'Message ID: {escape_drive_query(message_ids[0])}'"
    else:
        # Batch query with OR
        id_clauses = " or ".join(
            f"fullText contains 'Message ID: {escape_drive_query(mid)}'" for mid in message_ids
        )
        query = f"({id_clauses})"

    try:
        response = (
            service.files()
            .list(
                q=query,
                fields="files(id,name,mimeType,description)",
                pageSize=1000,  # Should be plenty for a batch
            )
            .execute()
        )

        files = response.get("files", [])

        # Group files by message ID (extracted from description)
        for f in files:
            description = f.get("description", "")
            # Extract message ID from description
            for line in description.split("\n"):
                if line.startswith("Message ID:"):
                    msg_id = line.split(":", 1)[1].strip()
                    if msg_id in message_ids:
                        if msg_id not in result:
                            result[msg_id] = []
                        result[msg_id].append({
                            "file_id": f["id"],
                            "name": f["name"],
                            "mimeType": f.get("mimeType", ""),
                        })
                    break

    except Exception:
        pass  # Silently fail - pre-exfil is optional optimization

    return result


# =============================================================================
# COMMENTS
# =============================================================================

# Fields for comments API — nested structure for author and replies
COMMENT_FIELDS = (
    "comments("
    "id,content,"
    "author(displayName,emailAddress),"
    "createdTime,modifiedTime,"
    "resolved,quotedFileContent,"
    "mentionedEmailAddresses,"
    "replies(id,content,author(displayName,emailAddress),createdTime,modifiedTime,mentionedEmailAddresses)"
    ")"
)


# MIME types that don't support comments (return 404 from Comments API)
COMMENT_UNSUPPORTED_MIMES = {
    "application/vnd.google-apps.form",
    "application/vnd.google-apps.shortcut",
    "application/vnd.google-apps.site",
    "application/vnd.google-apps.map",
    "application/vnd.google-apps.script",
}


@with_retry(max_attempts=3, delay_ms=1000)
def fetch_file_comments(
    file_id: str,
    include_deleted: bool = False,
    include_resolved: bool = True,
    max_results: int = 100,
) -> FileCommentsData:
    """
    Fetch comments from a Google Drive file.

    Uses Drive API comments endpoint. Comments include the comment text,
    author (name and email), anchor context (what text was highlighted),
    and any replies.

    Note: Some file types (Forms, Shortcuts, Sites, Maps, Apps Script) don't
    support comments and will raise MiseError with INVALID_INPUT.

    Args:
        file_id: The file ID
        include_deleted: Include deleted comments (default: False)
        include_resolved: Include resolved comments (default: True).
            Set to False to get only unresolved/open comments.
        max_results: Maximum comments to return (default: 100)

    Returns:
        FileCommentsData with all comments and replies

    Raises:
        MiseError: On API failure or unsupported file type
    """
    from googleapiclient.errors import HttpError

    service = get_drive_service()

    # Get file metadata first — also validates file exists
    metadata = get_file_metadata(file_id)
    file_name = metadata.get("name", "")
    mime_type = metadata.get("mimeType", "")

    # Check for known unsupported types before hitting the API
    if mime_type in COMMENT_UNSUPPORTED_MIMES:
        raise MiseError(
            ErrorKind.INVALID_INPUT,
            f"Comments not supported for {mime_type.split('.')[-1]} files",
            details={"file_id": file_id, "name": file_name, "mimeType": mime_type},
        )

    comments: list[CommentData] = []
    warnings: list[str] = []
    page_token = None

    try:
        while True:
            response = (
                service.comments()
                .list(
                    fileId=file_id,
                    fields=COMMENT_FIELDS,
                    includeDeleted=include_deleted,
                    pageSize=min(max_results - len(comments), 100),  # API max is 100
                    pageToken=page_token,
                )
                .execute()
            )

            for comment in response.get("comments", []):
                # Parse author
                author = comment.get("author", {})
                author_name = author.get("displayName")
                if not author_name:
                    author_name = "Unknown"
                    warnings.append(f"Comment {comment.get('id')}: missing author name")
                author_email = author.get("emailAddress")

                # Parse replies
                replies: list[CommentReply] = []
                for reply in comment.get("replies", []):
                    reply_author = reply.get("author", {})
                    reply_author_name = reply_author.get("displayName")
                    if not reply_author_name:
                        reply_author_name = "Unknown"
                        warnings.append(f"Reply {reply.get('id')}: missing author name")

                    replies.append(
                        CommentReply(
                            id=reply.get("id", ""),
                            content=reply.get("content", ""),
                            author_name=reply_author_name,
                            author_email=reply_author.get("emailAddress"),
                            created_time=reply.get("createdTime"),
                            modified_time=reply.get("modifiedTime"),
                            mentioned_emails=reply.get("mentionedEmailAddresses", []),
                        )
                    )

                # Parse quoted text (anchor)
                quoted_text = comment.get("quotedFileContent", {}).get("value", "")

                comments.append(
                    CommentData(
                        id=comment.get("id", ""),
                        content=comment.get("content", ""),
                        author_name=author_name,
                        author_email=author_email,
                        created_time=comment.get("createdTime"),
                        modified_time=comment.get("modifiedTime"),
                        resolved=comment.get("resolved", False),
                        quoted_text=quoted_text,
                        mentioned_emails=comment.get("mentionedEmailAddresses", []),
                        replies=replies,
                    )
                )

            # Check for more pages
            page_token = response.get("nextPageToken")
            if not page_token or len(comments) >= max_results:
                break

    except HttpError as e:
        # 404 from Comments API means file type doesn't support comments
        # (even though file exists — we already got metadata)
        if e.resp.status == 404:
            raise MiseError(
                ErrorKind.INVALID_INPUT,
                f"Comments not supported for this file type ({mime_type})",
                details={"file_id": file_id, "name": file_name, "mimeType": mime_type},
            )
        # Convert other HTTP errors to MiseError for consistent handling
        elif e.resp.status == 403:
            raise MiseError(
                ErrorKind.PERMISSION_DENIED,
                f"No access to comments on: {file_name}",
                details={"file_id": file_id, "http_status": 403},
            )
        elif e.resp.status == 429:
            raise MiseError(
                ErrorKind.RATE_LIMITED,
                "API quota exceeded for comments",
                details={"file_id": file_id, "http_status": 429},
                retryable=True,
            )
        elif e.resp.status >= 500:
            raise MiseError(
                ErrorKind.NETWORK_ERROR,
                f"Google API server error: {e.resp.status}",
                details={"file_id": file_id, "http_status": e.resp.status},
                retryable=True,
            )
        # Re-raise other HTTP errors wrapped in MiseError
        raise MiseError(
            ErrorKind.NETWORK_ERROR,
            f"HTTP error fetching comments: {e.resp.status}",
            details={"file_id": file_id, "http_status": e.resp.status},
        )

    # Filter out resolved comments if requested
    if not include_resolved:
        comments = [c for c in comments if not c.resolved]

    return FileCommentsData(
        file_id=file_id,
        file_name=file_name,
        comments=comments,
        warnings=warnings,
    )
