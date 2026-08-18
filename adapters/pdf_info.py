"""
The poppler subprocess seam — page counting (pdfinfo) and text extraction
(pdftotext). A sibling of pdf.py so the frozen module pays only its call
sites (_LEGACY_SIZE_BASELINE).

Page count is the ground truth that page-marker fidelity is judged against
(mise-wujoga): form-feed survival through extraction is per-PDF, not
per-path — markitdown kept the fixture's marker and dropped all 255 of the
BBC annual report's in the same environment — so the count has to be
measured while the bytes are in hand.

pdftotext -layout is the PDF text primary (mise-mitoki, census 2026-08-17:
97.4% verbatim value survival vs markitdown's 93.0%, 100% on big-table
pages, ~55× faster, and it emits the form feeds page citations ride on).

count_pdf_pages degrades to None wherever pdf2image or the poppler system
package is absent (slim build; mise-releko) — callers treat None as
"unknown", never as a page count. run_pdftotext raises instead (the caller
owns the fallback chain and the teaching warning).
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from logging_config import logger

# BBC annual report (256pp) extracts in ~1s on tube-class hardware; the
# ceiling is for pathological PDFs on slow disks, not a working budget.
PDFTOTEXT_TIMEOUT_S = 120

# GUI-spawned processes on macOS run with a bare launchd PATH that omits
# /opt/homebrew/bin (same mechanism as the uv fallback in ensure-mise.sh),
# so PATH failure is not proof of absence — probe the install homes too.
# Measured live 2026-08-18: the Mac has brew poppler, invisible to a
# non-interactive shell's PATH.
_PDFTOTEXT_HOMES = (
    "/opt/homebrew/bin/pdftotext",
    "/usr/local/bin/pdftotext",
    "/usr/bin/pdftotext",
)


def _pdftotext_bin() -> str:
    """Resolve the pdftotext binary: PATH first, then known install homes."""
    found = shutil.which("pdftotext")
    if found:
        return found
    for cand in _PDFTOTEXT_HOMES:
        if os.access(cand, os.X_OK):
            return cand
    raise FileNotFoundError(
        "pdftotext not found on PATH or in known install locations"
    )


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


def run_pdftotext(
    file_bytes: bytes | None = None,
    *,
    file_path: Path | None = None,
) -> str:
    """Extract PDF text via poppler's ``pdftotext -layout``.

    -layout preserves table columns as whitespace alignment (the census's
    winning format) and separates pages with form feeds.

    Raises FileNotFoundError when the binary is absent (slim host without
    poppler-utils) and ValueError on a non-zero exit (corrupt/encrypted
    PDF) — the caller owns the fallback chain, so failures here must be
    loud, never an empty string that reads as a thin text layer.
    """
    tmp_created = False
    if file_path is None:
        if file_bytes is None:
            raise ValueError("Must provide either file_bytes or file_path")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            file_path = Path(tmp.name)
        tmp_created = True

    try:
        proc = subprocess.run(
            [_pdftotext_bin(), "-layout", "-enc", "UTF-8", str(file_path), "-"],
            capture_output=True,
            timeout=PDFTOTEXT_TIMEOUT_S,
        )
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", errors="replace").strip()[:200]
            raise ValueError(f"pdftotext exit {proc.returncode}: {detail}")
        return proc.stdout.decode("utf-8", errors="replace")
    finally:
        if tmp_created:
            file_path.unlink(missing_ok=True)
