"""
PDF extraction adapter — hybrid markitdown + Drive conversion.

Strategy:
1. Try markitdown first (fast, ~1-5s, handles simple text PDFs)
2. If extraction is poor (<threshold chars), fall back to Drive conversion
   (slower, ~10-20s, but handles complex/image-heavy PDFs)

This gives fast results for simple PDFs while ensuring quality on complex ones.
"""

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from markitdown import MarkItDown

from adapters.conversion import convert_via_drive
from adapters.drive import download_file, download_file_to_temp, get_file_size, STREAMING_THRESHOLD_BYTES


# Default threshold for fallback to Drive conversion.
# Determined empirically (Jan 2026):
# - Simple text PDFs (reports, contracts): markitdown extracts 1000s-10000s chars
# - Complex/image-heavy PDFs (slides, scanned docs): markitdown extracts <100 chars
# - 500 chars is the midpoint with safety margin
# - Drive conversion extracts 100-1000x more from complex PDFs but is 5-10x slower
# If this threshold is too low: unnecessary slow conversions for simple PDFs
# If this threshold is too high: missed content from complex PDFs
DEFAULT_MIN_CHARS_THRESHOLD = 500


@dataclass
class PdfExtractionResult:
    """Result of PDF extraction."""
    content: str
    method: Literal["markitdown", "drive"]
    char_count: int
    warnings: list[str] = field(default_factory=list)


def extract_pdf_content(
    file_bytes: bytes | None = None,
    file_id: str = "",
    min_chars_threshold: int = DEFAULT_MIN_CHARS_THRESHOLD,
    *,
    file_path: Path | None = None,
) -> PdfExtractionResult:
    """
    Extract text from PDF using hybrid strategy.

    Accepts either file_bytes (in-memory) or file_path (from disk).
    Use file_path for large files to avoid memory issues.

    Args:
        file_bytes: Raw PDF content (mutually exclusive with file_path)
        file_id: Optional file ID (for temp file naming if Drive fallback needed)
        min_chars_threshold: Minimum chars to consider markitdown successful
        file_path: Path to PDF on disk (mutually exclusive with file_bytes)

    Returns:
        PdfExtractionResult with content and extraction method used
    """
    if file_bytes is None and file_path is None:
        raise ValueError("Must provide either file_bytes or file_path")
    if file_bytes is not None and file_path is not None:
        raise ValueError("Cannot provide both file_bytes and file_path")

    warnings: list[str] = []

    # 1. Try markitdown first (fast path)
    if file_path is not None:
        md = MarkItDown()
        result = md.convert_local(str(file_path))
        content = result.text_content or ""
    else:
        content = _extract_with_markitdown(file_bytes)
    char_count = len(content.strip())

    # 2. If markitdown produced enough content, we're done
    if char_count >= min_chars_threshold:
        return PdfExtractionResult(
            content=content,
            method="markitdown",
            char_count=char_count,
            warnings=warnings,
        )

    # 3. Markitdown failed — fall back to Drive conversion
    warnings.append(
        f"Markitdown extracted only {char_count} chars (threshold: {min_chars_threshold}), "
        "falling back to Drive conversion"
    )

    conversion_result = convert_via_drive(
        file_bytes=file_bytes,
        file_path=file_path,
        source_mime="application/pdf",
        target_type="doc",
        export_format="markdown",
        file_id_hint=file_id,
    )

    # Collect conversion warnings
    warnings.extend(conversion_result.warnings)

    return PdfExtractionResult(
        content=conversion_result.content,
        method="drive",
        char_count=len(conversion_result.content.strip()),
        warnings=warnings,
    )


def fetch_and_extract_pdf(
    file_id: str,
    min_chars_threshold: int = DEFAULT_MIN_CHARS_THRESHOLD,
) -> PdfExtractionResult:
    """
    Download PDF from Drive and extract content.

    Convenience function that combines download + extraction.
    Handles large files by streaming to temp file.

    Args:
        file_id: Drive file ID
        min_chars_threshold: Minimum chars to consider markitdown successful

    Returns:
        PdfExtractionResult with content and extraction method used
    """
    # Check file size to determine download strategy
    file_size = get_file_size(file_id)

    if file_size > STREAMING_THRESHOLD_BYTES:
        # Large file: stream to temp, extract from path
        return _fetch_and_extract_pdf_large(file_id, min_chars_threshold)
    else:
        # Small file: load into memory
        pdf_bytes = download_file(file_id)
        return extract_pdf_content(
            file_bytes=pdf_bytes,
            file_id=file_id,
            min_chars_threshold=min_chars_threshold,
        )


def _fetch_and_extract_pdf_large(
    file_id: str,
    min_chars_threshold: int = DEFAULT_MIN_CHARS_THRESHOLD,
) -> PdfExtractionResult:
    """
    Extract large PDF using streaming download.

    Downloads to temp file, delegates to extract_pdf_content(file_path=...),
    then cleans up.
    """
    tmp_path = download_file_to_temp(file_id, suffix=".pdf")

    try:
        result = extract_pdf_content(
            file_id=file_id,
            min_chars_threshold=min_chars_threshold,
            file_path=tmp_path,
        )
        result.warnings.insert(0, "Large file: using streaming download")
        return result
    finally:
        tmp_path.unlink(missing_ok=True)


def _extract_with_markitdown(pdf_bytes: bytes) -> str:
    """
    Extract PDF content using markitdown.

    Writes to temp file (markitdown requires file path), extracts, cleans up.
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = Path(tmp.name)

    try:
        md = MarkItDown()
        result = md.convert_local(str(tmp_path))
        return result.text_content or ""
    finally:
        tmp_path.unlink(missing_ok=True)
