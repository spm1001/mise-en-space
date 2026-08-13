"""
Cheap PDF page counting via poppler's pdfinfo — no rendering.

A sibling of pdf.py so the frozen module pays only its call sites
(_LEGACY_SIZE_BASELINE). Page count is the ground truth that page-marker
fidelity is judged against (mise-wujoga): form-feed survival through
extraction is per-PDF, not per-path — markitdown kept the fixture's marker
and dropped all 255 of the BBC annual report's in the same environment —
so the count has to be measured while the bytes are in hand.

Degrades to None wherever pdf2image or the poppler system package is
absent (slim build; mise-releko) — callers treat None as "unknown", never
as a page count.
"""

from pathlib import Path

from logging_config import logger


def count_pdf_pages(
    file_bytes: bytes | None = None,
    *,
    file_path: Path | None = None,
) -> int | None:
    """Return the PDF's page count via pdfinfo, or None if unavailable."""
    try:
        from pdf2image import pdfinfo_from_bytes, pdfinfo_from_path

        if file_path is not None:
            info = pdfinfo_from_path(str(file_path))
        elif file_bytes is not None:
            info = pdfinfo_from_bytes(file_bytes)
        else:
            return None
        pages = info.get("Pages")
        return int(pages) if pages else None
    except Exception as e:
        logger.debug("pdfinfo page count unavailable: %s", e)
        return None
