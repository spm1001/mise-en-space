"""
Drive conversion adapter — upload, convert, export, cleanup.

Shared infrastructure for PDF and Office file extraction.
Both use Drive's implicit conversion: upload with target mimeType → auto-converts.
"""

from dataclasses import dataclass, field
from typing import Literal
import logging

from googleapiclient.http import MediaInMemoryUpload

from adapters.services import get_drive_service
from adapters.drive import GOOGLE_DOC_MIME, GOOGLE_SHEET_MIME, GOOGLE_SLIDES_MIME
from retry import with_retry


logger = logging.getLogger(__name__)


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
    file_bytes: bytes,
    source_mime: str,
    target_type: Literal["doc", "sheet", "slides"],
    export_format: Literal["markdown", "csv", "plain"] = "markdown",
    temp_name_prefix: str = "_mise_temp_",
    file_id_hint: str = "",
) -> ConversionResult:
    """
    Convert file via Drive: upload with conversion, export, delete temp.

    This leverages Drive's implicit conversion — when you upload a file with
    a Google Workspace mimeType as target, Drive converts automatically.

    Args:
        file_bytes: Raw file content
        source_mime: Original file MIME type (e.g., 'application/pdf')
        target_type: Google format to convert to ('doc', 'sheet', 'slides')
        export_format: Format to export as ('markdown', 'csv', 'plain')
        temp_name_prefix: Prefix for temp file name (for debugging orphans)
        file_id_hint: Optional ID hint for temp file naming

    Returns:
        ConversionResult with content and cleanup status
    """
    service = get_drive_service()
    warnings: list[str] = []

    target_mime = CONVERSION_TARGETS[target_type]
    export_mime = EXPORT_MIMES[export_format]

    # 1. Upload with conversion
    temp_name = f"{temp_name_prefix}{file_id_hint}" if file_id_hint else temp_name_prefix
    media = MediaInMemoryUpload(file_bytes, mimetype=source_mime)

    uploaded = (
        service.files()
        .create(
            body={"name": temp_name, "mimeType": target_mime},
            media_body=media,
            fields="id",
        )
        .execute()
    )
    temp_id = uploaded["id"]

    try:
        # 2. Export to target format
        content = (
            service.files()
            .export(fileId=temp_id, mimeType=export_mime)
            .execute()
        )

        # Decode if bytes
        if isinstance(content, bytes):
            content = content.decode("utf-8")

    finally:
        # 3. Always attempt to delete temp file
        deleted = _delete_temp_file(service, temp_id, temp_name)
        if not deleted:
            warnings.append(f"Failed to delete temp file: {temp_name} (ID: {temp_id})")

    return ConversionResult(
        content=content,
        temp_file_deleted=deleted,
        warnings=warnings,
    )


def _delete_temp_file(service, file_id: str, file_name: str) -> bool:
    """
    Delete temporary file from Drive. Best-effort, logs failures.

    Returns:
        True if deleted, False if failed
    """
    try:
        service.files().delete(fileId=file_id).execute()
        return True
    except Exception as e:
        logger.warning(f"Failed to delete temp file {file_name} ({file_id}): {e}")
        return False
