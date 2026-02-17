"""
Office file extraction adapter — DOCX, XLSX, PPTX via Drive conversion.

Strategy: Upload with conversion to Google format, then export.
This produces cleaner output than local conversion tools, especially for XLSX.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TYPE_CHECKING, cast

from adapters.conversion import convert_via_drive, upload_and_convert, delete_temp_file
from adapters.drive import download_file, download_file_to_temp, get_file_size, STREAMING_THRESHOLD_BYTES
from adapters.sheets import fetch_spreadsheet
from extractors.sheets import extract_sheets_content

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
class OfficeExtractionResult:
    """Result of Office file extraction."""
    content: str
    source_type: OfficeType
    export_format: str  # 'markdown', 'csv', 'plain'
    extension: str      # 'md', 'csv', 'txt'
    warnings: list[str] = field(default_factory=list)
    spreadsheet_data: SpreadsheetData | None = None  # XLSX: carries tab data for per-tab deposit


def extract_office_content(
    office_type: OfficeType,
    file_bytes: bytes | None = None,
    file_path: Path | None = None,
    file_id: str = "",
    source_file_id: str | None = None,
) -> OfficeExtractionResult:
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
        OfficeExtractionResult with content and format info
    """
    source_mime, target_type, export_format, extension = OFFICE_FORMATS[office_type]

    # XLSX: use Sheets API to get all tabs (Drive CSV export is first-tab-only)
    if office_type == "xlsx":
        return _extract_xlsx_via_sheets_api(
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

    return OfficeExtractionResult(
        content=conversion_result.content,
        source_type=office_type,
        export_format=export_format,
        extension=extension,
        warnings=conversion_result.warnings,
    )


def _extract_xlsx_via_sheets_api(
    source_mime: str,
    file_bytes: bytes | None = None,
    file_path: Path | None = None,
    file_id: str = "",
    source_file_id: str | None = None,
) -> OfficeExtractionResult:
    """
    Extract XLSX via Sheets API: upload+convert, read all tabs, delete temp.

    Drive CSV export only returns the first sheet. The Sheets API path gives
    us all tabs with proper per-sheet CSV formatting via extract_sheets_content().
    """
    warnings: list[str] = []

    # Step 1: upload and convert to Google Sheet
    temp_id = upload_and_convert(
        file_bytes=file_bytes,
        file_path=file_path,
        source_mime=source_mime,
        target_type="sheet",
        file_id_hint=file_id,
        source_file_id=source_file_id,
    )

    try:
        # Step 2: read all tabs via Sheets API (no chart rendering for temp files)
        spreadsheet_data = fetch_spreadsheet(temp_id, render_charts=False)

        # Step 3: extract content (gets all tabs with === Sheet: Name === headers)
        content = extract_sheets_content(spreadsheet_data)
        warnings.extend(spreadsheet_data.warnings)

    finally:
        # Step 4: always clean up temp file
        if not delete_temp_file(temp_id, f"_mise_temp_{file_id}"):
            warnings.append(f"Failed to delete temp file (ID: {temp_id})")

    return OfficeExtractionResult(
        content=content,
        source_type="xlsx",
        export_format="csv",
        extension="csv",
        warnings=warnings,
        spreadsheet_data=spreadsheet_data,
    )


def fetch_and_extract_office(
    file_id: str,
    office_type: OfficeType,
) -> OfficeExtractionResult:
    """
    Download Office file from Drive and extract content.

    Convenience function that combines download + extraction.
    Handles large files by streaming to temp file.

    Args:
        file_id: Drive file ID
        office_type: 'docx', 'xlsx', or 'pptx'

    Returns:
        OfficeExtractionResult with content and format info
    """
    # Check file size to determine download strategy
    file_size = get_file_size(file_id)
    suffix = f".{office_type}"

    if file_size > STREAMING_THRESHOLD_BYTES:
        # Large file: stream to temp, pass path (not bytes) to avoid OOM
        tmp_path = download_file_to_temp(file_id, suffix=suffix)
        try:
            result = extract_office_content(
                file_path=tmp_path,
                office_type=office_type,
                file_id=file_id,
            )
            result.warnings.insert(0, "Large file: used streaming download")
            return result
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        # Small file: load into memory
        file_bytes = download_file(file_id)
        return extract_office_content(
            file_bytes=file_bytes,
            office_type=office_type,
            file_id=file_id,
        )


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
