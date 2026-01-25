"""
Office file extraction adapter — DOCX, XLSX, PPTX via Drive conversion.

Strategy: Upload with conversion to Google format, then export.
This produces cleaner output than local conversion tools, especially for XLSX.
"""

from dataclasses import dataclass, field
from typing import Literal, cast

from adapters.conversion import convert_via_drive
from adapters.drive import download_file, download_file_to_temp, get_file_size, STREAMING_THRESHOLD_BYTES


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


def extract_office_content(
    file_bytes: bytes,
    office_type: OfficeType,
    file_id: str = "",
) -> OfficeExtractionResult:
    """
    Extract content from Office file via Drive conversion.

    Args:
        file_bytes: Raw Office file content
        office_type: 'docx', 'xlsx', or 'pptx'
        file_id: Optional file ID (for temp file naming)

    Returns:
        OfficeExtractionResult with content and format info
    """
    source_mime, target_type, export_format, extension = OFFICE_FORMATS[office_type]

    # Convert via Drive
    # Cast to Literal types (OFFICE_FORMATS values are constants)
    conversion_result = convert_via_drive(
        file_bytes=file_bytes,
        source_mime=source_mime,
        target_type=cast(Literal["doc", "sheet", "slides"], target_type),
        export_format=cast(Literal["markdown", "csv", "plain"], export_format),
        file_id_hint=file_id,
    )

    return OfficeExtractionResult(
        content=conversion_result.content,
        source_type=office_type,
        export_format=export_format,
        extension=extension,
        warnings=conversion_result.warnings,
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
        # Large file: stream to temp
        tmp_path = download_file_to_temp(file_id, suffix=suffix)
        try:
            file_bytes = tmp_path.read_bytes()
            result = extract_office_content(
                file_bytes=file_bytes,
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
