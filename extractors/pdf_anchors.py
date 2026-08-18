"""
Grep-able exhibit anchors for vision-only graphics (mise-jopohi).

The census (636 probes, 70 real PDFs, 2026-08-17) measured ~3% of values as
vision-only — printed inside embedded chart images, reachable by NO text
extractor. The crops close the gap; these anchors are how a TEXT-first
reader discovers them: a single grep-able line in content.md at the point
of omission, naming the crop file to view. Placement is at eye level
deliberately — routing hints in manifest.json are read and ignored
(spec 2: obeyed 0/10), hints in the text body work.

Anchors carry ONLY deterministic fields (file, page, dimensions). The
consumer-side spec (Garni's ingestion letter) also wants entities, metrics
and an insight summary — those require *understanding* the graphic, which
a deterministic extractor cannot supply without fabricating; a consumer
with vision can enrich its own index from the crops.

Pure functions, no I/O (extractors layer). Crops arrive as plain dicts
(file/pages/width/height) because extractors never import adapters.
"""

from typing import Any

ANCHOR_PREFIX = "<!-- exhibit:"

_UNPLACED_HEADER = (
    "<!-- exhibits: the graphics below could not be placed at their pages "
    "(page markers absent from this extraction) — each anchor names its "
    "source page -->"
)


def anchor_line(file: str, page: int, width: int, height: int) -> str:
    """One self-contained, grep-able exhibit anchor.

    Single line by design: `grep exhibit: content.md` must return hits that
    each carry the crop filename and page without needing context lines.
    """
    return (
        f"{ANCHOR_PREFIX} {file} | page {page} | {width}x{height}px | "
        "embedded graphic — its values are NOT in this text; view the crop image -->"
    )


def insert_crop_anchors(content: str, crops: list[dict[str, Any]]) -> tuple[str, bool]:
    """
    Insert exhibit anchors into extracted PDF text.

    Placement rides the form-feed page markers (pdftotext emits them
    natively): each anchor lands at the END of the text of every page its
    graphic appears on. When the markers can't carry a crop's page —
    markitdown drops them per-PDF, the Drive path has no page concept —
    every anchor goes to a disclosed block at the document end instead;
    a wrong placement would be worse than a grouped one.

    Args:
        content: Extracted text, pages separated by \\f (or not).
        crops: Dicts with keys file, pages (list[int], 1-based),
            width, height.

    Returns:
        (content with anchors, placed_per_page) — False means the
        end-block fallback was used and the caller should disclose it.
    """
    if not crops:
        return content, True

    max_page = max(max(c["pages"]) for c in crops)
    segments = content.split("\f")

    if len(segments) >= max_page:
        for c in crops:
            for page in c["pages"]:
                line = anchor_line(c["file"], page, c["width"], c["height"])
                seg = segments[page - 1]
                segments[page - 1] = seg.rstrip("\n") + "\n\n" + line + "\n"
        return "\f".join(segments), True

    lines = [_UNPLACED_HEADER]
    for c in crops:
        for page in c["pages"]:
            lines.append(anchor_line(c["file"], page, c["width"], c["height"]))
    return content.rstrip("\n") + "\n\n" + "\n".join(lines) + "\n", False
