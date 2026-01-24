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
from adapters.drive import download_file


# Default threshold for fallback to Drive conversion.
# Determined empirically: simple text PDFs produce 1000s of chars,
# complex/image-heavy PDFs produce <100 chars with markitdown.
DEFAULT_MIN_CHARS_THRESHOLD = 500


@dataclass
class PdfExtractionResult:
    """Result of PDF extraction."""
    content: str
    method: Literal["markitdown", "drive"]
    char_count: int
    warnings: list[str] = field(default_factory=list)


def extract_pdf_content(
    file_bytes: bytes,
    file_id: str = "",
    min_chars_threshold: int = DEFAULT_MIN_CHARS_THRESHOLD,
) -> PdfExtractionResult:
    """
    Extract text from PDF using hybrid strategy.

    Args:
        file_bytes: Raw PDF content
        file_id: Optional file ID (for temp file naming if Drive fallback needed)
        min_chars_threshold: Minimum chars to consider markitdown successful

    Returns:
        PdfExtractionResult with content and extraction method used
    """
    warnings: list[str] = []

    # 1. Try markitdown first (fast path)
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

    Args:
        file_id: Drive file ID
        min_chars_threshold: Minimum chars to consider markitdown successful

    Returns:
        PdfExtractionResult with content and extraction method used
    """
    # Download the PDF
    pdf_bytes = download_file(file_id)

    # Extract content
    return extract_pdf_content(
        file_bytes=pdf_bytes,
        file_id=file_id,
        min_chars_threshold=min_chars_threshold,
    )


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
