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
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from logging_config import logger

# BBC annual report (256pp) extracts in ~1s on tube-class hardware; the
# ceiling is for pathological PDFs on slow disks, not a working budget.
PDFTOTEXT_TIMEOUT_S = 120

def _pdftotext_bin() -> str:
    """Resolve the pdftotext binary (see _poppler_bin for the PATH story)."""
    return _poppler_bin("pdftotext")


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


# --- Embedded-graphic crops (mise-jopohi) -----------------------------------
# The census measured ~3% of PDF values as vision-only — printed inside
# embedded chart images (Excel charts pasted as pictures) that no text
# extractor can reach. These crops are the producer half of hybrid
# ingestion: extract the embedded raster objects themselves, at original
# resolution, so a vision-capable consumer can read the pixels.
#
# The filter is CALIBRATED ON THE CENSUS CORPUS (118 real PDFs + the 20
# adjudicated vision-only probes, 2026-08-18): min dimension >= 240px and
# page spread <= 3 drop logos/banners/furniture; page coverage >= 0.8 drops
# full-slide background photos (which page thumbnails already carry, and
# which an encoding-based filter got wrong — a real chart shipped as JPEG).
# Result on the corpus: median 3.5 crops/doc, p90 44; all 12 chart-value
# probes covered; the 6 uncovered probes are watermark badges baked into
# backgrounds — the class the census routes to page thumbnails. There is
# deliberately NO count cap: an evicted chart is a silently-lost value,
# and disk in a dot-dir is cheap (never narrow a fetch to save context).
_CROP_MIN_DIM_PX = 240
_CROP_MAX_PAGE_SPREAD = 3
_CROP_MAX_PAGE_COVERAGE = 0.8
PDFIMAGES_TIMEOUT_S = 120


@dataclass
class PdfCrop:
    """One embedded graphic extracted from a PDF."""
    name: str          # deposit filename, e.g. crop_p008_i012.png
    pages: list[int]   # 1-based pages the graphic appears on (<= spread cap)
    width: int
    height: int
    png_bytes: bytes


def _poppler_bin(name: str) -> str:
    """Resolve a poppler binary: PATH first, then known install homes.

    GUI-spawned processes on macOS run with a bare launchd PATH that omits
    /opt/homebrew/bin (same mechanism as the uv fallback in ensure-mise.sh),
    so a PATH miss is not proof of absence. Measured live 2026-08-18: the
    Mac has brew poppler, invisible to a non-interactive shell's PATH.
    """
    found = shutil.which(name)
    if found:
        return found
    for home in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        cand = f"{home}/{name}"
        if os.access(cand, os.X_OK):
            return cand
    raise FileNotFoundError(
        f"{name} not found on PATH or in known install locations"
    )


def _page_area_in2(file_path: Path) -> float | None:
    """Page-1 area in square inches via pdfinfo, None when unavailable.

    Mixed-size documents are approximated by page 1 — the coverage test
    only needs to tell full-page backgrounds from sub-page graphics.
    """
    try:
        proc = subprocess.run(
            [_poppler_bin("pdfinfo"), str(file_path)],
            capture_output=True, timeout=30,
        )
        m = re.search(
            r"Page size:\s+([\d.]+) x ([\d.]+)",
            proc.stdout.decode("utf-8", errors="replace"),
        )
        if not m:
            return None
        return (float(m.group(1)) / 72.0) * (float(m.group(2)) / 72.0)
    except Exception:
        return None


def _select_crop_objects(
    objects: Iterable[dict[str, Any]], page_area: float | None
) -> list[dict[str, Any]]:
    """Apply the corpus-calibrated filter (see the constants block above).

    objects: dicts with num/pages/w/h/xppi/yppi. Pure so the thresholds
    are testable without poppler.
    """
    selected = []
    for rec in objects:
        if min(rec["w"], rec["h"]) < _CROP_MIN_DIM_PX:
            continue
        if len(rec["pages"]) > _CROP_MAX_PAGE_SPREAD:
            continue
        if page_area and rec["xppi"] > 0 and rec["yppi"] > 0:
            img_area = (rec["w"] / rec["xppi"]) * (rec["h"] / rec["yppi"])
            if img_area / page_area >= _CROP_MAX_PAGE_COVERAGE:
                continue
        selected.append(rec)
    return selected


def extract_pdf_crops(
    file_bytes: bytes | None = None,
    *,
    file_path: Path | None = None,
) -> list[PdfCrop]:
    """
    Extract qualifying embedded graphics as PNG crops.

    Raises FileNotFoundError when poppler is absent and ValueError on a
    pdfimages failure — the caller owns the never-blocks-the-fetch wrap.

    Boundary, measured not guessed: this reaches embedded RASTER objects
    only. Vector-drawn charts and values baked into full-page background
    photos stay reachable via page thumbnails.
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
        listing = subprocess.run(
            [_poppler_bin("pdfimages"), "-list", str(file_path)],
            capture_output=True, timeout=PDFIMAGES_TIMEOUT_S,
        )
        if listing.returncode != 0:
            detail = listing.stderr.decode("utf-8", errors="replace").strip()[:200]
            raise ValueError(f"pdfimages -list exit {listing.returncode}: {detail}")

        # Parse ALL rows: `num` indexes extraction output across image AND
        # smask/stencil rows (verified: N list rows <-> N extracted files),
        # but only type==image rows are crop candidates.
        by_obj: dict[str, dict[str, Any]] = {}
        for ln in listing.stdout.decode("utf-8", errors="replace").splitlines()[2:]:
            parts = ln.split()
            if len(parts) < 14 or parts[2] != "image":
                continue
            page, num = int(parts[0]), int(parts[1])
            key = parts[10] if parts[10] != "[inline]" else f"inline-{page}-{num}"
            rec = by_obj.setdefault(key, {
                "num": num, "pages": set(),
                "w": int(parts[3]), "h": int(parts[4]),
                "xppi": float(parts[12]), "yppi": float(parts[13]),
            })
            rec["pages"].add(page)

        selected = _select_crop_objects(by_obj.values(), _page_area_in2(file_path))
        if not selected:
            return []

        with tempfile.TemporaryDirectory() as out_dir:
            extract = subprocess.run(
                [_poppler_bin("pdfimages"), "-png", str(file_path), f"{out_dir}/img"],
                capture_output=True, timeout=PDFIMAGES_TIMEOUT_S,
            )
            if extract.returncode != 0:
                detail = extract.stderr.decode("utf-8", errors="replace").strip()[:200]
                raise ValueError(f"pdfimages -png exit {extract.returncode}: {detail}")

            crops = []
            for rec in sorted(selected, key=lambda r: (min(r["pages"]), r["num"])):
                src = Path(out_dir) / f"img-{rec['num']:03d}.png"
                if not src.exists():
                    logger.debug("crop %s missing from pdfimages output", src.name)
                    continue
                first_page = min(rec["pages"])
                crops.append(PdfCrop(
                    name=f"crop_p{first_page:03d}_i{rec['num']:03d}.png",
                    pages=sorted(rec["pages"]),
                    width=rec["w"],
                    height=rec["h"],
                    png_bytes=src.read_bytes(),
                ))
            return crops
    finally:
        if tmp_created:
            file_path.unlink(missing_ok=True)
