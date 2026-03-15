"""
Drive conversion adapter — upload, convert, export, cleanup.

Shared infrastructure for PDF and Office file extraction.
Both use Drive's implicit conversion: upload with target mimeType → auto-converts.

Uses httpx via MiseSyncClient (Phase 1 migration). Will switch to
MiseHttpClient (async) when the tools/server layer goes async.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator, Literal
import logging

from adapters.http_client import get_sync_client
from adapters.drive import GOOGLE_DOC_MIME, GOOGLE_SHEET_MIME, GOOGLE_SLIDES_MIME
from retry import with_retry


logger = logging.getLogger(__name__)


# Drive API v3 base URLs
_DRIVE_API = "https://www.googleapis.com/drive/v3/files"
_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"


@dataclass
class ConversionResult:
    """Result of Drive conversion."""
    content: str
    temp_file_deleted: bool
    warnings: list[str] = field(default_factory=list)


# Target Google MIME types for conversion
CONVERSION_TARGETS = {
    "doc": GOOGLE_DOC_MIME,
    "sheet": GOOGLE_SHEET_MIME,
    "slides": GOOGLE_SLIDES_MIME,
}

# Export MIME types
EXPORT_MIMES = {
    "markdown": "text/markdown",
    "csv": "text/csv",
    "plain": "text/plain",
}


@with_retry(max_attempts=3, delay_ms=1000)
def convert_via_drive(
    file_bytes: bytes | None = None,
    source_mime: str = "",
    target_type: Literal["doc", "sheet", "slides"] = "doc",
    export_format: Literal["markdown", "csv", "plain"] = "markdown",
    temp_name_prefix: str = "_mise_temp_",
    file_id_hint: str = "",
    file_path: Path | None = None,
    source_file_id: str | None = None,
) -> ConversionResult:
    """
    Convert file via Drive: upload with conversion, export, delete temp.

    This leverages Drive's implicit conversion — when you upload a file with
    a Google Workspace mimeType as target, Drive converts automatically.

    Accepts either file_bytes (in-memory), file_path (streaming from disk),
    or source_file_id (file already in Drive — copies with conversion,
    skipping both download and upload).

    Args:
        file_bytes: Raw file content (mutually exclusive with file_path/source_file_id)
        source_mime: Original file MIME type (e.g., 'application/pdf')
        target_type: Google format to convert to ('doc', 'sheet', 'slides')
        export_format: Format to export as ('markdown', 'csv', 'plain')
        temp_name_prefix: Prefix for temp file name (for debugging orphans)
        file_id_hint: Optional ID hint for temp file naming
        file_path: Path to file on disk (mutually exclusive with file_bytes/source_file_id)
        source_file_id: Drive file ID to copy+convert (skips upload entirely)

    Returns:
        ConversionResult with content and cleanup status

    Raises:
        ValueError: If no source provided or conflicting sources
    """
    client = get_sync_client()
    warnings: list[str] = []

    target_mime = CONVERSION_TARGETS[target_type]
    export_mime = EXPORT_MIMES[export_format]
    temp_name = f"{temp_name_prefix}{file_id_hint}" if file_id_hint else temp_name_prefix

    # 1. Get file into Drive as Google format
    if source_file_id:
        # File already in Drive — copy with conversion (no upload needed)
        copied = client.post_json(
            f"{_DRIVE_API}/{source_file_id}/copy",
            json_body={"name": temp_name, "mimeType": target_mime},
            params={"fields": "id"},
        )
        temp_id = copied["id"]
    else:
        # Upload with conversion (from disk or in-memory)
        if file_bytes is None and file_path is None:
            raise ValueError("Must provide file_bytes, file_path, or source_file_id")
        if file_bytes is not None and file_path is not None:
            raise ValueError("Cannot provide both file_bytes and file_path")

        if file_path is not None:
            upload_bytes = file_path.read_bytes()
        else:
            upload_bytes = file_bytes

        metadata = {"name": temp_name, "mimeType": target_mime}
        uploaded = client.upload_multipart(
            _UPLOAD_API, metadata, upload_bytes, source_mime,
            params={"uploadType": "multipart", "fields": "id"},
        )
        temp_id = uploaded["id"]

    try:
        # 2. Export to target format
        content_bytes = client.get_bytes(
            f"{_DRIVE_API}/{temp_id}/export",
            params={"mimeType": export_mime},
        )
        content = content_bytes.decode("utf-8")

    finally:
        # 3. Always attempt to delete temp file
        deleted = _delete_temp_file(client, temp_id, temp_name)
        if not deleted:
            warnings.append(f"Failed to delete temp file: {temp_name} (ID: {temp_id})")

    return ConversionResult(
        content=content,
        temp_file_deleted=deleted,
        warnings=warnings,
    )


@with_retry(max_attempts=3, delay_ms=1000)
def upload_and_convert(
    file_bytes: bytes | None = None,
    source_mime: str = "",
    target_type: Literal["doc", "sheet", "slides"] = "doc",
    temp_name_prefix: str = "_mise_temp_",
    file_id_hint: str = "",
    file_path: Path | None = None,
    source_file_id: str | None = None,
) -> str:
    """
    Upload file to Drive with conversion, return temp file ID.

    Same as convert_via_drive step 1, but returns the temp ID instead of
    exporting. Caller is responsible for reading the converted file and
    calling delete_temp_file() when done.

    Used for XLSX → Sheets path where we need the Sheets API (not CSV export)
    to read all tabs.

    Returns:
        Temp file ID in Drive (as Google Workspace format)
    """
    client = get_sync_client()
    target_mime = CONVERSION_TARGETS[target_type]
    temp_name = f"{temp_name_prefix}{file_id_hint}" if file_id_hint else temp_name_prefix

    if source_file_id:
        copied = client.post_json(
            f"{_DRIVE_API}/{source_file_id}/copy",
            json_body={"name": temp_name, "mimeType": target_mime},
            params={"fields": "id"},
        )
        return copied["id"]
    else:
        if file_bytes is None and file_path is None:
            raise ValueError("Must provide file_bytes, file_path, or source_file_id")
        if file_bytes is not None and file_path is not None:
            raise ValueError("Cannot provide both file_bytes and file_path")

        if file_path is not None:
            upload_bytes = file_path.read_bytes()
        else:
            upload_bytes = file_bytes

        metadata = {"name": temp_name, "mimeType": target_mime}
        uploaded = client.upload_multipart(
            _UPLOAD_API, metadata, upload_bytes, source_mime,
            params={"uploadType": "multipart", "fields": "id"},
        )
        return uploaded["id"]


def delete_temp_file(file_id: str, file_name: str = "") -> bool:
    """
    Delete temporary file from Drive. Best-effort, logs failures.

    Public wrapper around _delete_temp_file for use by callers of
    upload_and_convert().
    """
    client = get_sync_client()
    return _delete_temp_file(client, file_id, file_name)


@contextmanager
def drive_temp_file(
    file_bytes: bytes | None = None,
    source_mime: str = "",
    target_type: Literal["doc", "sheet", "slides"] = "doc",
    temp_name_prefix: str = "_mise_temp_",
    file_id_hint: str = "",
    file_path: Path | None = None,
    source_file_id: str | None = None,
) -> Generator[str, None, None]:
    """Context manager wrapping upload_and_convert with guaranteed cleanup.

    Yields the temp file ID. Deletes the temp file in finally block,
    so callers can't forget cleanup even on exception.

    Usage:
        with drive_temp_file(file_bytes=data, source_mime="application/pdf") as temp_id:
            content = export(temp_id)
    """
    temp_id = upload_and_convert(
        file_bytes=file_bytes,
        source_mime=source_mime,
        target_type=target_type,
        temp_name_prefix=temp_name_prefix,
        file_id_hint=file_id_hint,
        file_path=file_path,
        source_file_id=source_file_id,
    )
    temp_name = f"{temp_name_prefix}{file_id_hint}" if file_id_hint else temp_name_prefix
    try:
        yield temp_id
    finally:
        deleted = delete_temp_file(temp_id, temp_name)
        if not deleted:
            logger.warning(f"Orphaned temp file: {temp_name} (ID: {temp_id})")


def _delete_temp_file(client: Any, file_id: str, file_name: str) -> bool:
    """
    Delete temporary file from Drive. Best-effort, logs failures.

    Returns:
        True if deleted, False if failed
    """
    try:
        client.delete(f"{_DRIVE_API}/{file_id}")
        return True
    except Exception as e:
        logger.warning(f"Failed to delete temp file {file_name} ({file_id}): {e}")
        return False


def cleanup_orphaned_temp_files() -> int:
    """Find and delete orphaned _mise_temp_* files in Drive.

    Best-effort cleanup — logs failures, doesn't raise.
    Returns count of files successfully deleted.
    """
    client = get_sync_client()
    deleted = 0
    try:
        result = client.get_json(
            _DRIVE_API,
            params={
                "q": "name contains '_mise_temp_' and trashed = false",
                "fields": "files(id, name, createdTime)",
                "pageSize": 50,
            },
        )
        files = result.get("files", [])
        if not files:
            return 0

        logger.info(f"Found {len(files)} orphaned _mise_temp_* files in Drive")
        for f in files:
            if _delete_temp_file(client, f["id"], f["name"]):
                deleted += 1

        if deleted:
            logger.info(f"Cleaned up {deleted}/{len(files)} orphaned temp files")
    except Exception as e:
        logger.warning(f"Orphan cleanup failed: {e}")

    return deleted
