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
from typing import Any, Literal

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


# --- Thumbnail rendering constants ---
TARGET_MAX_PX = 1568   # Anthropic vision API max dimension before downscale
FIXED_DPI = 150        # pdf2image path — good enough for A4, API handles slight overshoot
MAX_DPI = 200          # CoreGraphics cap for tiny pages
MAX_THUMBNAIL_PAGES = 100


@dataclass
class PageImage:
    """A single rendered page thumbnail."""
    page_index: int      # 0-based
    image_bytes: bytes   # PNG
    width_px: int
    height_px: int


@dataclass
class PdfThumbnailResult:
    """Result of rendering PDF pages to thumbnails."""
    pages: list[PageImage]
    page_count: int      # Total pages in PDF (may > len(pages) on cap or partial failure)
    method: str          # "coregraphics" or "pdf2image"
    warnings: list[str] = field(default_factory=list)


@dataclass
class PdfExtractionResult:
    """Result of PDF extraction."""
    content: str
    method: Literal["markitdown", "drive"]
    char_count: int
    warnings: list[str] = field(default_factory=list)
    thumbnails: PdfThumbnailResult | None = None


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
        result = extract_pdf_content(
            file_bytes=pdf_bytes,
            file_id=file_id,
            min_chars_threshold=min_chars_threshold,
        )
        try:
            result.thumbnails = render_pdf_pages(file_bytes=pdf_bytes)
        except Exception as e:
            result.warnings.append(f"Thumbnail rendering failed: {e}")
        return result


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
        # Render thumbnails before temp file is unlinked
        try:
            result.thumbnails = render_pdf_pages(file_path=tmp_path)
        except Exception as e:
            result.warnings.append(f"Thumbnail rendering failed: {e}")
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


def _calculate_dpi(page_w_pts: float, page_h_pts: float) -> int:
    """
    Calculate DPI to render a page so its longest side fits TARGET_MAX_PX.

    CoreGraphics only — pdf2image uses a fixed DPI for all pages.
    Points are 1/72 inch, so page dimensions in inches = pts/72.

    Returns DPI capped at MAX_DPI (tiny pages don't need extreme resolution).
    Falls back to FIXED_DPI if dimensions are zero.
    """
    if page_w_pts <= 0 or page_h_pts <= 0:
        return FIXED_DPI
    max_pts = max(page_w_pts, page_h_pts)
    max_inches = max_pts / 72.0
    dpi = TARGET_MAX_PX / max_inches
    return min(int(dpi), MAX_DPI)


def render_pdf_pages(
    file_bytes: bytes | None = None,
    *,
    file_path: Path | None = None,
) -> PdfThumbnailResult:
    """
    Render PDF pages to PNG thumbnails.

    Platform dispatch:
    - macOS: try CoreGraphics (fast, per-page DPI) → fall back to pdf2image
    - Linux: pdf2image (requires poppler-utils system package)

    Args:
        file_bytes: Raw PDF content (mutually exclusive with file_path)
        file_path: Path to PDF on disk (mutually exclusive with file_bytes)

    Returns:
        PdfThumbnailResult with rendered pages

    Raises:
        ImportError: If no rendering backend is available
        Various: Backend-specific errors (caught by callers)
    """
    import sys

    if file_bytes is None and file_path is None:
        raise ValueError("Must provide either file_bytes or file_path")

    if sys.platform == "darwin":
        try:
            return _render_via_coregraphics(file_bytes, file_path=file_path)
        except ImportError:
            log.info("PyObjC not available, falling back to pdf2image")

    return _render_via_pdf2image(file_bytes, file_path=file_path)


def _render_via_coregraphics(
    file_bytes: bytes | None = None,
    *,
    file_path: Path | None = None,
) -> PdfThumbnailResult:
    """
    Render PDF pages via macOS CoreGraphics (Quartz).

    Per-page DPI calculation for optimal resolution.
    Requires PyObjC (pyobjc-framework-Quartz).
    CoreGraphics needs a file path — if given bytes, writes to temp first.
    """
    import Quartz  # type: ignore[import-untyped]

    # CoreGraphics needs a file path
    tmp_created = False
    if file_path is None:
        assert file_bytes is not None
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            file_path = Path(tmp.name)
        tmp_created = True

    try:
        return _do_coregraphics_render(file_path, Quartz)
    finally:
        if tmp_created:
            file_path.unlink(missing_ok=True)


def _do_coregraphics_render(file_path: Path, Quartz: Any) -> PdfThumbnailResult:
    """Core rendering logic using Quartz framework."""
    import CoreFoundation  # type: ignore[import-untyped]

    url = CoreFoundation.CFURLCreateFromFileSystemRepresentation(
        None, str(file_path).encode(), len(str(file_path).encode()), False
    )
    pdf_doc = Quartz.CGPDFDocumentCreateWithURL(url)
    if pdf_doc is None:
        raise ValueError(f"CoreGraphics could not open PDF: {file_path}")

    total_pages = Quartz.CGPDFDocumentGetNumberOfPages(pdf_doc)
    warnings: list[str] = []
    render_count = min(total_pages, MAX_THUMBNAIL_PAGES)
    if total_pages > MAX_THUMBNAIL_PAGES:
        warnings.append(f"Thumbnails limited to first {MAX_THUMBNAIL_PAGES} of {total_pages} pages")

    pages: list[PageImage] = []
    for i in range(1, render_count + 1):  # CGPDFDocument is 1-indexed
        page = Quartz.CGPDFDocumentGetPage(pdf_doc, i)
        if page is None:
            warnings.append(f"Page {i} could not be read")
            continue

        media_box = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFMediaBox)
        page_w = media_box.size.width
        page_h = media_box.size.height
        dpi = _calculate_dpi(page_w, page_h)

        # Scale factor: DPI / 72 (points are 1/72 inch)
        scale = dpi / 72.0
        width_px = int(page_w * scale)
        height_px = int(page_h * scale)

        # Create bitmap context
        cs = Quartz.CGColorSpaceCreateDeviceRGB()
        ctx = Quartz.CGBitmapContextCreate(
            None, width_px, height_px, 8, width_px * 4,
            cs, Quartz.kCGImageAlphaPremultipliedLast,
        )

        # White background
        Quartz.CGContextSetRGBFillColor(ctx, 1.0, 1.0, 1.0, 1.0)
        Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, width_px, height_px))

        # Scale and draw
        Quartz.CGContextScaleCTM(ctx, scale, scale)
        Quartz.CGContextDrawPDFPage(ctx, page)

        # Export to PNG via temp file
        image = Quartz.CGBitmapContextCreateImage(ctx)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_png:
            tmp_png_path = Path(tmp_png.name)

        png_url = CoreFoundation.CFURLCreateFromFileSystemRepresentation(
            None, str(tmp_png_path).encode(), len(str(tmp_png_path).encode()), False
        )
        dest = Quartz.CGImageDestinationCreateWithURL(png_url, "public.png", 1, None)
        Quartz.CGImageDestinationAddImage(dest, image, None)
        Quartz.CGImageDestinationFinalize(dest)

        png_bytes = tmp_png_path.read_bytes()
        tmp_png_path.unlink(missing_ok=True)

        pages.append(PageImage(
            page_index=i - 1,
            image_bytes=png_bytes,
            width_px=width_px,
            height_px=height_px,
        ))

    return PdfThumbnailResult(
        pages=pages,
        page_count=total_pages,
        method="coregraphics",
        warnings=warnings,
    )


def _render_via_pdf2image(
    file_bytes: bytes | None = None,
    *,
    file_path: Path | None = None,
) -> PdfThumbnailResult:
    """
    Render PDF pages via pdf2image (poppler backend).

    Uses fixed DPI for all pages. Requires poppler-utils system package.
    """
    import io
    from pdf2image import convert_from_bytes, convert_from_path  # type: ignore[import-untyped]
    from pdf2image.exceptions import (  # type: ignore[import-untyped]
        PDFInfoNotInstalledError,
        PDFPageCountError,
    )

    warnings: list[str] = []

    try:
        if file_path is not None:
            pil_images = convert_from_path(
                str(file_path),
                dpi=FIXED_DPI,
                fmt="png",
                last_page=MAX_THUMBNAIL_PAGES,
            )
        elif file_bytes is not None:
            pil_images = convert_from_bytes(
                file_bytes,
                dpi=FIXED_DPI,
                fmt="png",
                last_page=MAX_THUMBNAIL_PAGES,
            )
        else:
            raise ValueError("Must provide either file_bytes or file_path")
    except PDFInfoNotInstalledError:
        raise ImportError(
            "poppler-utils not installed. Install with: "
            "apt-get install poppler-utils (Debian/Ubuntu) or "
            "brew install poppler (macOS)"
        )
    except PDFPageCountError as e:
        raise ValueError(f"Could not determine PDF page count: {e}")

    # Get total page count for the result
    # pdf2image may have capped at MAX_THUMBNAIL_PAGES, so we need the real count
    total_pages = len(pil_images)
    # If we got exactly MAX_THUMBNAIL_PAGES, there might be more — try to count
    if total_pages == MAX_THUMBNAIL_PAGES:
        try:
            from pdf2image import pdfinfo_from_bytes, pdfinfo_from_path  # type: ignore[import-untyped]
            if file_path is not None:
                info = pdfinfo_from_path(str(file_path))
            else:
                info = pdfinfo_from_bytes(file_bytes)
            real_count = info.get("Pages", total_pages)
            if real_count > MAX_THUMBNAIL_PAGES:
                total_pages = real_count
                warnings.append(
                    f"Thumbnails limited to first {MAX_THUMBNAIL_PAGES} of {total_pages} pages"
                )
        except Exception:
            pass  # Can't get real count — use what we have

    pages: list[PageImage] = []
    for i, pil_img in enumerate(pil_images):
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        pages.append(PageImage(
            page_index=i,
            image_bytes=png_bytes,
            width_px=pil_img.width,
            height_px=pil_img.height,
        ))

    return PdfThumbnailResult(
        pages=pages,
        page_count=total_pages,
        method="pdf2image",
        warnings=warnings,
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
