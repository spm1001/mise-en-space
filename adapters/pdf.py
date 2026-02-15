"""
PDF extraction adapter — hybrid markitdown + Drive conversion.

Strategy:
1. Try markitdown first (fast, ~1-5s, handles simple text PDFs)
2. If extraction is poor (<threshold chars), fall back to Drive conversion
   (slower, ~10-20s, but handles complex/image-heavy PDFs)

This gives fast results for simple PDFs while ensuring quality on complex ones.
"""

import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from markitdown import MarkItDown

from adapters.conversion import convert_via_drive
from adapters.drive import download_file, download_file_to_temp, get_file_size, STREAMING_THRESHOLD_BYTES

log = logging.getLogger(__name__)

# Default threshold for fallback to Drive conversion.
# Determined empirically (Jan 2026):
# - Simple text PDFs (reports, contracts): markitdown extracts 1000s-10000s chars
# - Complex/image-heavy PDFs (slides, scanned docs): markitdown extracts <100 chars
# - 500 chars is the midpoint with safety margin
# - Drive conversion extracts 100-1000x more from complex PDFs but is 5-10x slower
# If this threshold is too low: unnecessary slow conversions for simple PDFs
# If this threshold is too high: missed content from complex PDFs
DEFAULT_MIN_CHARS_THRESHOLD = 500

# Flattened-table detection thresholds (empirical, Jan 2026).
# See plan cozy-gliding-mccarthy.md for false-positive analysis.
_FLAT_MIN_LINES = 20
_FLAT_SHORT_RATIO = 0.60   # lines with 1-3 tokens
_FLAT_SENTENCE_RATIO = 0.10  # lines with 6+ tokens
_FLAT_NUMERIC_RATIO = 0.15   # lines containing digits


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

    # 2. If markitdown produced enough content, check structural quality
    if char_count >= min_chars_threshold:
        if _looks_like_flattened_tables(content):
            warnings.append(
                f"Markitdown extracted {char_count} chars but content looks like "
                "flattened tables (no row/column structure), "
                "falling back to Drive conversion"
            )
        else:
            return PdfExtractionResult(
                content=content,
                method="markitdown",
                char_count=char_count,
                warnings=warnings,
            )

    # 3. Markitdown failed or produced flattened tables — fall back to Drive
    if not warnings:
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


def _looks_like_flattened_tables(content: str) -> bool:
    """
    Detect markitdown output that looks like flattened table data.

    Three-signal heuristic — all must fire:
    - high short_ratio: 60%+ lines are 1-3 tokens (one cell per line)
    - low sentence_ratio: <10% lines with 6+ tokens (no prose)
    - high numeric_ratio: 15%+ lines contain digits (data values)

    Guards: skip if <20 non-empty lines or content already has markdown
    table syntax (pipes), meaning markitdown preserved structure.
    """
    lines = [ln for ln in content.splitlines() if ln.strip()]
    if len(lines) < _FLAT_MIN_LINES:
        return False

    # If markitdown already produced table syntax, structure is preserved
    if any(ln.strip().startswith("|") and "|" in ln[1:] for ln in lines):
        return False

    short = sum(1 for ln in lines if len(ln.split()) <= 3)
    sentences = sum(1 for ln in lines if len(ln.split()) >= 6)
    numeric = sum(1 for ln in lines if re.search(r"\d", ln))

    n = len(lines)
    short_ratio = short / n
    sentence_ratio = sentences / n
    numeric_ratio = numeric / n

    is_flattened = (
        short_ratio >= _FLAT_SHORT_RATIO
        and sentence_ratio <= _FLAT_SENTENCE_RATIO
        and numeric_ratio >= _FLAT_NUMERIC_RATIO
    )

    if is_flattened:
        log.info(
            "Flattened table detected: short=%.2f sentence=%.2f numeric=%.2f (%d lines)",
            short_ratio, sentence_ratio, numeric_ratio, n,
        )

    return is_flattened


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
