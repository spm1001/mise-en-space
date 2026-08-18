"""
PDF page-thumbnail rendering — platform-adaptive.

- macOS: CoreGraphics via PyObjC (fast, per-page DPI) → falls back to pdf2image
- Linux: pdf2image (requires the poppler-utils system package; mise-releko)

Split from adapters/pdf.py 2026-08-18 (mise-mitoki): rendering and text
conversion are separate concerns, and the split let the size ratchet
tighten rather than grow. Text extraction lives in pdf.py; the poppler
subprocess seam (pdfinfo, pdftotext) in pdf_info.py.
"""

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

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
    import Quartz

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
    import CoreFoundation

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
    from pdf2image import convert_from_bytes, convert_from_path
    from pdf2image.exceptions import (
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
            from pdf2image import pdfinfo_from_bytes, pdfinfo_from_path
            if file_path is not None:
                info = pdfinfo_from_path(str(file_path))
            else:
                assert file_bytes is not None  # validated at function entry
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
