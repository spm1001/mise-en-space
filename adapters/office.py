"""
Office file conversion adapter — DOCX, XLSX, PPTX via Drive conversion.

Strategy: Upload with conversion to Google format, then export.
This produces cleaner output than local conversion tools, especially for XLSX.
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TYPE_CHECKING, cast

from adapters.conversion import convert_via_drive, drive_temp_file
from adapters.drive import download_file, download_file_to_temp, get_file_size, STREAMING_THRESHOLD_BYTES
from adapters.sheets import fetch_spreadsheet
from extractors.docx_markup import count_docx_markup, format_markup_warnings
from extractors.sheets import extract_sheets_content

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from models import SpreadsheetData


# Supported Office types and their conversion mappings
# (source_mime, google_target, export_format, output_extension)
OFFICE_FORMATS = {
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc",
        "markdown",
        "md",
    ),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "sheet",
        "csv",
        "csv",
    ),
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "slides",
        "plain",
        "txt",
    ),
}

OfficeType = Literal["docx", "xlsx", "pptx"]


@dataclass
class OfficeConversionResult:
    """Result of Office file extraction."""
    content: str
    source_type: OfficeType
    export_format: str  # 'markdown', 'csv', 'plain'
    extension: str      # 'md', 'csv', 'txt'
    warnings: list[str] = field(default_factory=list)
    spreadsheet_data: SpreadsheetData | None = None  # XLSX: carries tab data for per-tab deposit
    raw_bytes: bytes | None = None  # Original file bytes for raw deposit (XLSX, small files)
    raw_temp_path: Path | None = None  # Original file on disk for raw deposit (XLSX, large streamed files)


def convert_office_content(
    office_type: OfficeType,
    file_bytes: bytes | None = None,
    file_path: Path | None = None,
    file_id: str = "",
    source_file_id: str | None = None,
) -> OfficeConversionResult:
    """
    Extract content from Office file via Drive conversion.

    Accepts file_bytes (in-memory), file_path (from disk), or source_file_id
    (file already in Drive — copies with conversion, skipping upload entirely).

    XLSX uses a special path: upload+convert to Google Sheet, then read via
    Sheets API (gets all tabs). Drive CSV export only returns the first tab.

    Args:
        office_type: 'docx', 'xlsx', or 'pptx'
        file_bytes: Raw Office file content
        file_path: Path to Office file on disk
        file_id: Optional file ID (for temp file naming)
        source_file_id: Drive file ID to copy+convert (skips upload)

    Returns:
        OfficeConversionResult with content and format info
    """
    source_mime, target_type, export_format, extension = OFFICE_FORMATS[office_type]

    # XLSX: use Sheets API to get all tabs (Drive CSV export is first-tab-only)
    if office_type == "xlsx":
        return _convert_xlsx_via_sheets_api(
            source_mime=source_mime,
            file_bytes=file_bytes,
            file_path=file_path,
            file_id=file_id,
            source_file_id=source_file_id,
        )

    # DOCX/PPTX: standard Drive conversion path
    conversion_result = convert_via_drive(
        file_bytes=file_bytes,
        file_path=file_path,
        source_mime=source_mime,
        target_type=cast(Literal["doc", "sheet", "slides"], target_type),
        export_format=cast(Literal["markdown", "csv", "plain"], export_format),
        file_id_hint=file_id,
        source_file_id=source_file_id,
    )

    warnings = list(conversion_result.warnings)
    # Drive export flattens tracked changes, comments, and inline images
    # silently — a tracked-DELETED clause reads as present text. Warn so
    # the reader knows to go to source. Needs the raw bytes, so the
    # source_file_id path (server-side copy, nothing downloaded) is not
    # inspected — accepted MVP gap, see mise-kecigu.
    if office_type == "docx" and (file_bytes is not None or file_path is not None):
        warnings.extend(_inspect_docx_markup(file_bytes, file_path))

    return OfficeConversionResult(
        content=conversion_result.content,
        source_type=office_type,
        export_format=export_format,
        extension=extension,
        warnings=warnings,
    )


def _inspect_docx_markup(
    file_bytes: bytes | None,
    file_path: Path | None,
) -> list[str]:
    """
    Best-effort markup inspection of the original .docx archive.

    Never raises — a corrupt zip or unexpected layout must not fail the
    fetch; the warning layer is advisory.
    """
    try:
        source: io.BytesIO | Path
        source = io.BytesIO(file_bytes) if file_bytes is not None else cast(Path, file_path)
        with zipfile.ZipFile(source) as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                return []
            document_xml = archive.read("word/document.xml")
            comments_xml = (
                archive.read("word/comments.xml")
                if "word/comments.xml" in names
                else None
            )
        return format_markup_warnings(count_docx_markup(document_xml, comments_xml))
    except Exception:
        logger.debug("docx markup inspection failed; skipping warnings", exc_info=True)
        return []


def _convert_xlsx_via_sheets_api(
    source_mime: str,
    file_bytes: bytes | None = None,
    file_path: Path | None = None,
    file_id: str = "",
    source_file_id: str | None = None,
) -> OfficeConversionResult:
    """
    Extract XLSX via Sheets API: upload+convert, read all tabs, delete temp.

    Drive CSV export only returns the first sheet. The Sheets API path gives
    us all tabs with proper per-sheet CSV formatting via extract_sheets_content().
    """
    warnings: list[str] = []

    # Upload, convert, read, and auto-cleanup via context manager
    with drive_temp_file(
        file_bytes=file_bytes,
        file_path=file_path,
        source_mime=source_mime,
        target_type="sheet",
        file_id_hint=file_id,
        source_file_id=source_file_id,
    ) as temp_id:
        # Read all tabs via Sheets API (no chart rendering for temp files)
        spreadsheet_data = fetch_spreadsheet(temp_id, render_charts=False)

        # Extract content (gets all tabs with === Sheet: Name === headers)
        content = extract_sheets_content(spreadsheet_data)
        warnings.extend(spreadsheet_data.warnings)

    return OfficeConversionResult(
        content=content,
        source_type="xlsx",
        export_format="csv",
        extension="csv",
        warnings=warnings,
        spreadsheet_data=spreadsheet_data,
    )


def fetch_and_convert_office(
    file_id: str,
    office_type: OfficeType,
) -> OfficeConversionResult:
    """
    Download Office file from Drive and extract content.

    Convenience function that combines download + extraction.
    Handles large files by streaming to temp file.

    Args:
        file_id: Drive file ID
        office_type: 'docx', 'xlsx', or 'pptx'

    Returns:
        OfficeConversionResult with content and format info
    """
    # Check file size to determine download strategy
    file_size = get_file_size(file_id)
    suffix = f".{office_type}"

    if file_size > STREAMING_THRESHOLD_BYTES:
        # Large file: stream to temp, pass path (not bytes) to avoid OOM
        tmp_path = download_file_to_temp(file_id, suffix=suffix)
        try:
            result = convert_office_content(
                file_path=tmp_path,
                office_type=office_type,
                file_id=file_id,
            )
            result.warnings.insert(0, "Large file: used streaming download")
            # Carry temp path for xlsx raw deposit — tool layer copies directly,
            # avoiding read_bytes which would double peak memory for large files
            if office_type == "xlsx":
                result.raw_temp_path = tmp_path
            return result
        finally:
            # Only delete temp if tool layer isn't going to copy it
            if not (office_type == "xlsx"):
                tmp_path.unlink(missing_ok=True)
    else:
        # Small file: load into memory
        file_bytes = download_file(file_id)
        result = convert_office_content(
            file_bytes=file_bytes,
            office_type=office_type,
            file_id=file_id,
        )
        # Keep raw bytes for xlsx deposit
        if office_type == "xlsx":
            result.raw_bytes = file_bytes
        return result


def get_office_type_from_mime(mime_type: str) -> OfficeType | None:
    """
    Get Office type from MIME type.

    Args:
        mime_type: MIME type string

    Returns:
        'docx', 'xlsx', 'pptx', or None if not an Office type
    """
    for office_type_key, (source_mime, _, _, _) in OFFICE_FORMATS.items():
        if mime_type == source_mime:
            return cast(OfficeType, office_type_key)
    return None
