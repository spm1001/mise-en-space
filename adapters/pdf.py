"""
PDF conversion adapter.

Strategy (mise-mitoki, census 2026-08-17 — 636 probes over 70 real PDFs):
1. pdftotext -layout (poppler) — best verbatim survival on every variant
   (97.4% overall, 100% on big-table pages), ~55× faster than markitdown,
   page-true form feeds. Char threshold is its only quality gate.
2. markitdown — only when poppler is absent; behind the flattened-table
   detector, which is calibrated on markitdown's own failure shapes.
3. Drive server-side conversion — thin/no text layer (scans need OCR) or
   no local extractor available.
"""

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

try:
    from markitdown import MarkItDown
except ImportError:  # slim/embedded build — PDF text falls back to Drive conversion
    MarkItDown = None  # type: ignore[assignment,misc]

from adapters.conversion import convert_via_drive
from extractors.text_quality import looks_like_flattened_tables
from adapters.drive import download_file, download_file_to_temp, get_file_size, STREAMING_THRESHOLD_BYTES
from adapters.pdf_info import count_pdf_pages, run_pdftotext
from adapters.pdf_render import PdfThumbnailResult, render_pdf_pages

# Threshold for the local-extraction fallback to Drive conversion (empirical,
# Jan 2026): text PDFs yield 1000s of chars locally, scanned/image-only ones
# <100 — those need Drive's server-side OCR, which extracts 100-1000× more
# from them at 5-10× the wall-clock. 500 is the midpoint with margin.
DEFAULT_MIN_CHARS_THRESHOLD = 500


@dataclass
class PdfConversionResult:
    """Result of PDF extraction."""
    content: str
    method: Literal["pdftotext", "markitdown", "drive"]
    char_count: int
    warnings: list[str] = field(default_factory=list)
    thumbnails: PdfThumbnailResult | None = None
    pdf_pages: int | None = None  # poppler ground truth; None = unknown (mise-wujoga)


def convert_pdf_content(
    file_bytes: bytes | None = None,
    file_id: str = "",
    min_chars_threshold: int = DEFAULT_MIN_CHARS_THRESHOLD,
    *,
    file_path: Path | None = None,
) -> PdfConversionResult:
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
        PdfConversionResult with content and extraction method used
    """
    if file_bytes is None and file_path is None:
        raise ValueError("Must provide either file_bytes or file_path")
    if file_bytes is not None and file_path is not None:
        raise ValueError("Cannot provide both file_bytes and file_path")

    pdf_pages = count_pdf_pages(file_bytes=file_bytes, file_path=file_path)
    warnings: list[str] = []

    # 1. pdftotext -layout primary (mise-mitoki). The char threshold is its
    #    ONLY gate: the flattened-table detector is calibrated on markitdown's
    #    failure shapes and false-fires on -layout's whitespace-aligned columns
    #    (27/133 census slices, all good output), so it never judges poppler.
    try_markitdown = False
    try:
        content = run_pdftotext(file_bytes=file_bytes, file_path=file_path)
        char_count = len(content.strip())
        if char_count >= min_chars_threshold:
            return PdfConversionResult(
                content=content,
                method="pdftotext",
                char_count=char_count,
                warnings=warnings,
                pdf_pages=pdf_pages,
            )
        # Thin text layer: markitdown reads the same layer and cannot see
        # more — go straight to Drive's server-side OCR.
        warnings.append(
            f"pdftotext extracted only {char_count} chars (threshold: "
            f"{min_chars_threshold}) — likely scanned/image-only pages, "
            "falling back to Drive conversion"
        )
    except FileNotFoundError:
        warnings.append(
            "pdftotext not installed — PDF text quality degrades (tables, "
            "page markers). Install poppler-utils (Debian/Ubuntu) or "
            "poppler (brew)."
        )
        try_markitdown = True
    except Exception as e:
        warnings.append(f"pdftotext failed ({e}) — trying markitdown")
        try_markitdown = True

    # 2. markitdown, only when pdftotext couldn't run — behind its structural
    #    quality gate (the detector's own calibration corpus).
    if try_markitdown:
        if MarkItDown is None:
            warnings.append(
                "Local PDF extraction unavailable (embedded build) — "
                "converting via Drive server-side."
            )
        else:
            content = _convert_with_markitdown(file_bytes, file_path=file_path)
            char_count = len(content.strip())
            if char_count < min_chars_threshold:
                warnings.append(
                    f"Markitdown extracted only {char_count} chars (threshold: "
                    f"{min_chars_threshold}), falling back to Drive conversion"
                )
            elif looks_like_flattened_tables(content):
                warnings.append(
                    f"Markitdown extracted {char_count} chars but content looks "
                    "like flattened tables (no row/column structure), "
                    "falling back to Drive conversion"
                )
            else:
                return PdfConversionResult(
                    content=content,
                    method="markitdown",
                    char_count=char_count,
                    warnings=warnings,
                    pdf_pages=pdf_pages,
                )

    # 3. Drive server-side conversion — the OCR-capable fallback

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

    return PdfConversionResult(
        content=conversion_result.content,
        method="drive",
        char_count=len(conversion_result.content.strip()),
        warnings=warnings,
        pdf_pages=pdf_pages,
    )


def fetch_and_convert_pdf(
    file_id: str,
    min_chars_threshold: int = DEFAULT_MIN_CHARS_THRESHOLD,
    thumbnails: bool = True,
) -> PdfConversionResult:
    """
    Download PDF from Drive and extract content.

    Convenience function that combines download + extraction.
    Handles large files by streaming to temp file.

    Args:
        file_id: Drive file ID
        min_chars_threshold: Minimum chars to consider markitdown successful
        thumbnails: False skips page-thumbnail rendering entirely (mise-giwawa)

    Returns:
        PdfConversionResult with content and extraction method used
    """
    # Check file size to determine download strategy
    file_size = get_file_size(file_id)

    if file_size > STREAMING_THRESHOLD_BYTES:
        # Large file: stream to temp, extract from path
        return _fetch_and_convert_pdf_large(file_id, min_chars_threshold, thumbnails=thumbnails)
    else:
        # Small file: load into memory
        pdf_bytes = download_file(file_id)
        result = convert_pdf_content(
            file_bytes=pdf_bytes,
            file_id=file_id,
            min_chars_threshold=min_chars_threshold,
        )
        if thumbnails:
            try:
                result.thumbnails = render_pdf_pages(file_bytes=pdf_bytes)
            except Exception as e:
                result.warnings.append(f"Thumbnail rendering failed: {e}")
        return result


def _fetch_and_convert_pdf_large(
    file_id: str,
    min_chars_threshold: int = DEFAULT_MIN_CHARS_THRESHOLD,
    *, thumbnails: bool = True,
) -> PdfConversionResult:
    """
    Extract large PDF using streaming download.

    Downloads to temp file, delegates to convert_pdf_content(file_path=...),
    then cleans up.
    """
    tmp_path = download_file_to_temp(file_id, suffix=".pdf")

    try:
        result = convert_pdf_content(
            file_id=file_id,
            min_chars_threshold=min_chars_threshold,
            file_path=tmp_path,
        )
        result.warnings.insert(0, "Large file: using streaming download")
        # Render thumbnails before temp file is unlinked
        if thumbnails:
            try:
                result.thumbnails = render_pdf_pages(file_path=tmp_path)
            except Exception as e:
                result.warnings.append(f"Thumbnail rendering failed: {e}")
        return result
    finally:
        tmp_path.unlink(missing_ok=True)


def _convert_with_markitdown(
    file_bytes: bytes | None = None,
    *,
    file_path: Path | None = None,
) -> str:
    """Extract PDF content using markitdown (temp file when given bytes)."""
    assert MarkItDown is not None  # only called when the extraction extra is present
    tmp_created = False
    if file_path is None:
        assert file_bytes is not None  # caller validated the pair
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            file_path = Path(tmp.name)
        tmp_created = True

    try:
        md = MarkItDown()
        result = md.convert_local(str(file_path))
        return result.text_content or ""
    finally:
        if tmp_created:
            file_path.unlink(missing_ok=True)
