"""
Shared helpers for fetch sub-modules.

Contains _build_cues, _build_email_context_metadata,
_enrich_with_comments, and text file detection.
"""

from pathlib import Path
from typing import Any, Sequence

from adapters.comment_anchors import (
    fetch_sheets_comment_anchors,
    fetch_slides_comment_anchors,
)
from adapters.drive import fetch_file_comments
from adapters.pdf import PdfConversionResult
from extractors.comment_anchors import AnchorLocator, sheets_locators, slides_locators
from extractors.comments import extract_comments_content
from tools.suggestions import count_untaggable_suggestions
from extractors.pdf_anchors import insert_crop_anchors
from extractors.sheets import extract_sheets_per_tab
from models import MiseError, EmailContext, SlideData
from workspace import write_content, write_page_thumbnail, slugify


def _anchor_locators(
    file_id: str, surface: str, slides: Sequence[SlideData] | None
) -> tuple[dict[str, AnchorLocator] | None, str | None]:
    """Read the anchored-comments preview map for one file.

    Returns (locators, unavailable_reason) — exactly one is set. Called ONLY
    when the file has open comments (the checkbox-oracle cost pattern: an extra
    API call per fetch is worth it on the files that have something to locate,
    and free on the ones that don't).
    """
    if surface == "slides":
        read = fetch_slides_comment_anchors(file_id)
        if read.payload is None:
            return (None, read.reason)
        deck = [(s.slide_id, s.title) for s in (slides or [])]
        return (slides_locators(read.payload, deck), None)
    read = fetch_sheets_comment_anchors(file_id)
    if read.payload is None:
        return (None, read.reason)
    return (sheets_locators(read.payload), None)


def _enrich_with_comments(
    file_id: str,
    folder: Path,
    document_markdown: str | None = None,
    *,
    surface: str | None = None,
    slides: Sequence[SlideData] | None = None,
    warnings: list[str] | None = None,
) -> tuple[int, str | None]:
    """
    Fetch open comments and write to deposit folder.

    Sous-chef philosophy: bring everything chef needs without being asked.

    Args:
        file_id: Drive file ID
        folder: Deposit folder path
        document_markdown: The fetched doc's content (Docs only). When supplied,
            comments are located in the document tree and ordered by document
            position.
        surface: "slides" or "sheet" to add per-slide / per-cell locators from
            the anchored-comments preview read (mise-dukacu). None keeps the
            flat API-order render.
        slides: the deck's SlideData in order — gives locators the deposit's own
            slide numbering and titles.
        warnings: the caller's warnings list, appended to (not returned) when
            enrichment degrades. This is what carries the reason into cues.

    Returns:
        Tuple of (open_comment_count, comments_md or None)

    Degradation is deliberate and disclosed: a file type that has no comments
    API, or a preview read an unenrolled caller cannot make, must never fail a
    fetch — but every fallback says why it fired.
    """
    try:
        data = fetch_file_comments(file_id, include_resolved=False, max_results=100)
        if not data.comments:
            return (0, None)

        locators: dict[str, AnchorLocator] | None = None
        if surface:
            locators, reason = _anchor_locators(file_id, surface, slides)
            if reason and warnings is not None:
                warnings.append(
                    f"Comment locators unavailable ({reason}) — comments.md lists "
                    "comments in API order with no slide/cell locators."
                )

        # Extract to markdown
        comments_md = extract_comments_content(
            data, document_markdown=document_markdown, locators=locators
        )
        if data.warnings and warnings is not None:
            warnings.extend(data.warnings)

        # Write to deposit folder
        write_content(folder, comments_md, filename="comments.md")

        return (data.comment_count, comments_md)
    except MiseError as e:
        # Designed failure: file types with no comments API (Forms, Sites,
        # Shortcuts…), or no permission to read them. Named, not silent.
        if warnings is not None:
            warnings.append(f"Comments not read for this file: {e}")
        return (0, None)
    except Exception as e:  # noqa: BLE001 — enrichment must never kill a fetch
        # NOT a designed failure. Fail open, but say so — a bare swallow here
        # renders "no comments" and "the comments read broke" identically, which
        # is precisely how a comment goes to the void unnoticed.
        if warnings is not None:
            warnings.append(
                f"Comments could not be read: {type(e).__name__}: {e} — "
                "comments.md is absent from this deposit, not empty."
            )
        return (0, None)


def _write_per_tab_csvs(
    folder: Path, data: Any, *, tabs_info: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Write per-tab CSV files and return the manifest's tab index.

    Returns one {name, sheet_id, filename} entry per tab — including for a
    single-tab sheet, whose entry names content.csv (the whole-sheet file IS
    that tab; no per-tab file is written for it). Per-tab files are written
    only when there are 2+ tabs. sheet_id is Google's numeric id (what a URL's
    ?gid= names) and None for xlsx-sourced data (mise-dogape).
    Mutates tabs_info list if provided, otherwise creates new.
    """
    per_tab = extract_sheets_per_tab(data)
    result = tabs_info if tabs_info is not None else []
    if len(per_tab) <= 1:
        for tab in data.sheets:
            result.append(
                {"name": tab.name, "sheet_id": tab.sheet_id, "filename": "content.csv"}
            )
        return result

    for tab, (tab_name, csv_content) in zip(data.sheets, per_tab):
        tab_slug = slugify(tab_name, max_length=40)
        filename = f"content_{tab_slug}.csv"
        write_content(folder, csv_content, filename=filename)
        result.append(
            {"name": tab_name, "sheet_id": tab.sheet_id, "filename": filename}
        )
    return result


def _deposit_pdf_thumbnails(
    folder: Path,
    result: PdfConversionResult,
) -> dict[str, Any]:
    """
    Write PDF page thumbnails to deposit folder and return manifest extras.

    Shared by drive, web, and gmail fetch paths. Returns a dict of
    thumbnail-related fields to merge into the manifest.

    Returns empty dict if no thumbnails available.
    """
    if not result.thumbnails:
        return {}

    thumbnail_count = 0
    for page_img in result.thumbnails.pages:
        write_page_thumbnail(folder, page_img.image_bytes, page_img.page_index)
        thumbnail_count += 1

    extras: dict[str, Any] = {
        "page_count": result.thumbnails.page_count,
        "has_thumbnails": thumbnail_count > 0,
        "thumbnail_count": thumbnail_count,
        "thumbnail_method": result.thumbnails.method,
    }

    # Track pages that failed to render (gaps between expected and actual)
    rendered_indices = {p.page_index for p in result.thumbnails.pages}
    expected_count = min(result.thumbnails.page_count, 100)
    missing = [i + 1 for i in range(expected_count) if i not in rendered_indices]
    if missing:
        extras["thumbnail_failures"] = missing

    return extras


def add_file_provenance(extra: dict[str, Any], metadata: dict[str, Any]) -> None:
    """Add created/modified timestamps + last-modifier to a manifest extra dict.

    last_modified_by rides here because every Drive deposit route funnels
    through this helper: Shared Drive files have NO owners, so the
    last-modifier is the only honest author signal a manifest can carry
    (mise-tanoti — Garni's MANIFEST 'modified <date> by <who>' line).
    """
    if metadata.get("createdTime"):
        extra["created_time"] = metadata["createdTime"]
    if metadata.get("modifiedTime"):
        extra["modified_time"] = metadata["modifiedTime"]
    lmu = metadata.get("lastModifyingUser", {})
    modifier = lmu.get("displayName") or lmu.get("emailAddress")
    if modifier:
        extra["last_modified_by"] = modifier


def deposit_pdf_crops(folder: Path, result: PdfConversionResult) -> dict[str, Any]:
    """
    Write embedded-graphic crops and anchor them in the content (mise-jopohi).

    Mutates result.content (exhibit anchors at each crop's page — call
    BEFORE write_content) and result.warnings (the repo's warnings
    pattern). Returns manifest extras; empty dict when there are no crops.

    The anchors are the interface: a text-first reader greps content.md,
    hits `<!-- exhibit: crop_pNNN_iNNN.png … -->` at the point of omission,
    and views the crop. Manifest `crops` entries carry the same records for
    programmatic consumers.
    """
    if not result.crops:
        return {}

    records = []
    for crop in result.crops:
        (Path(folder) / crop.name).write_bytes(crop.png_bytes)
        records.append({
            "file": crop.name,
            "pages": crop.pages,
            "width": crop.width,
            "height": crop.height,
        })

    result.content, placed = insert_crop_anchors(result.content, records)
    if not placed:
        result.warnings.append(
            f"{len(records)} graphic crops extracted, but page markers are "
            "absent from this extraction — exhibit anchors are grouped at "
            "the end of content.md rather than at their pages."
        )
    return {"crops": records, "crop_count": len(records)}


def pdf_page_fidelity(result: PdfConversionResult) -> dict[str, Any]:
    """
    Manifest extras for page-citation viability, plus a loud warning when
    citations can't be derived (mise-wujoga).

    Page-marker survival is per-PDF, not per-path: markitdown kept the
    two-page fixture's form feed and dropped all 255 of the BBC annual
    report's in the same environment, and the Drive-conversion fallback
    (PDF → temp Doc → markdown) structurally has no page concept. So the
    contract is measured, never inferred from extraction_method: count the
    markers actually present in the content, judge them against poppler's
    page count (result.pdf_pages, None = unknown), and warn — by mutating
    result.warnings, the repo's warnings pattern — whenever a page-citing
    consumer would silently mis-cite.

    Full preservation is pages-1 or pages markers (trailing form feed
    varies); 0 markers on a multi-page PDF means citations are underivable,
    and anything in between means they'd MISALIGN, which is worse than
    absent — both warn.
    """
    markers = result.content.count("\f")
    extras: dict[str, Any] = {"page_markers": markers}
    pages = result.pdf_pages
    if pages is not None:
        extras["pdf_pages"] = pages

    if pages is not None and pages >= 2:
        if markers == 0:
            result.warnings.append(
                f"No page markers survived extraction ({pages}-page PDF, "
                f"{result.method} path) — per-page citations cannot be "
                "derived from content.md."
            )
        elif markers < pages - 1:
            result.warnings.append(
                f"Partial page markers: {markers} form feeds for {pages} "
                f"pages ({result.method} path) — per-page citations would "
                "misalign; treat page boundaries as unreliable."
            )
    elif pages is None and result.method == "drive" and markers == 0:
        result.warnings.append(
            "PDF extracted via Drive conversion, which preserves no page "
            "boundaries — per-page citations cannot be derived (page count "
            "unknown on this build)."
        )

    return extras


def _build_cues(
    folder: Path | str,
    *,
    open_comment_count: int = 0,
    warnings: list[str] | None = None,
    email_context: EmailContext | None = None,
    participants: list[str] | None = None,
    has_attachments: bool | None = None,
    date_range: str | None = None,
    tab_names: list[str] | None = None,
    formula_count: int | None = None,
    merged_cell_count: int | None = None,
) -> dict[str, Any]:
    """
    Build cues dict for FetchResult — decision-tree signals for the caller.

    Cues surface actionable information so callers don't need to read
    manifest.json or Glob the deposit folder. Explicit nulls mean
    "we checked, nothing found" (not "we didn't check").
    """
    folder_path = Path(folder) if isinstance(folder, str) else folder

    # Single pass: list files and find content length
    file_names: list[str] = []
    thumbnail_names: list[str] = []
    content_length = 0
    if folder_path.exists():
        for f in folder_path.iterdir():
            if f.is_file():
                name = f.name
                if (name.startswith("slide_") or name.startswith("page_")) and name.endswith(".png"):
                    thumbnail_names.append(name)
                else:
                    file_names.append(name)
                if name.startswith("content."):
                    content_length = f.stat().st_size

    # Collapse thumbnails into a compact summary
    files = sorted(file_names)
    if thumbnail_names:
        sorted_thumbs = sorted(thumbnail_names)
        if len(sorted_thumbs) > 3:
            files.append(f"{sorted_thumbs[0]} ... {sorted_thumbs[-1]} ({len(sorted_thumbs)} thumbnails)")
        else:
            files.extend(sorted_thumbs)

    cues: dict[str, Any] = {
        "files": files,
        "open_comment_count": open_comment_count,
        "warnings": warnings or [],
        "content_length": content_length,
        "email_context": (
            _build_email_context_metadata(email_context) if email_context else None
        ),
    }

    # Sheet-specific cues
    if formula_count is not None:
        cues["formula_count"] = formula_count

    if merged_cell_count is not None and merged_cell_count > 0:
        cues["merged_cell_count"] = merged_cell_count

    if tab_names is not None:
        cues["tab_count"] = len(tab_names)
        cues["tab_names"] = tab_names

    # Gmail-specific cues
    if participants is not None:
        cues["participants"] = participants
    if has_attachments is not None:
        cues["has_attachments"] = has_attachments
    if date_range is not None:
        cues["date_range"] = date_range

    return cues


# Text MIME types that can be downloaded and deposited directly
TEXT_MIME_TYPES = {
    "text/plain",
    "text/csv",
    "text/markdown",
    "text/html",
    "text/xml",
    "application/json",
    "application/xml",
    "application/x-yaml",
    "text/x-python",
    "text/javascript",
    "application/javascript",
}


def is_text_file(mime_type: str) -> bool:
    """Check if MIME type is a text-based format we can handle directly."""
    if mime_type in TEXT_MIME_TYPES:
        return True
    # Also handle any text/* type not explicitly listed
    if mime_type.startswith("text/"):
        return True
    return False


def _build_email_context_metadata(email_context: EmailContext | None) -> dict[str, Any] | None:
    """Build email_context dict for FetchResult metadata.

    Shape and hint text live on EmailContext.to_cue() — the search path mints the same
    block and the two copies drifted apart in wording risk until 2026-08-02 (mise-saroca).
    """
    if not email_context:
        return None
    return email_context.to_cue()


def suggestion_cues(doc_data: Any) -> dict[str, Any]:
    """Suggestion cues for a Doc fetch — including the ones mise cannot render.

    `suggestion_count` counts TEXT suggestions, because those are the only kind
    that can be shown as CriticMarkup and folded by an [sN] tag. A document
    whose only pending suggestions are formatting (a Word `w:rPrChange` becomes
    `suggestedTextStyleChanges`) therefore produced no cue at all, and read as
    settled when it was not — measured on a real .docx import (essayeur,
    mise-hupago). Disclose them separately rather than counting them into a
    total mise cannot act on.
    """
    cues: dict[str, Any] = {}
    if doc_data.suggestion_count > 0:
        cues["has_suggestions"] = True
        cues["suggestion_count"] = doc_data.suggestion_count
        cues["suggestions_mode"] = doc_data.suggestions_mode
    untaggable = count_untaggable_suggestions(doc_data.tabs)
    if untaggable:
        cues["has_suggestions"] = True
        cues["formatting_suggestions"] = untaggable
        cues["formatting_suggestions_note"] = (
            f"{untaggable} pending FORMATTING suggestion(s) (bold, style, "
            "bullets) exist that mise cannot render as markup or fold by [sN] "
            "tag — this document is NOT settled. Accept or reject them in the "
            "Docs UI."
        )
    return cues
