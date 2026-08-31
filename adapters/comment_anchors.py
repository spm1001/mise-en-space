"""
Comment-anchor reads — Slides and Sheets, Google Developer Preview (mise-dukacu).

Two thin GETs that ask the *document* APIs for the anchor→object map the Drive
comments plane cannot give us. Drive's `comments.list` returns an `anchor`
string per thread; on Sheets that string carries an opaque range uid (the real
`GridRange` exists only here), and on Slides the shape-anchor spelling produced
by UI-authored comments was never measured on the Drive plane. So this module is
the single source of locator truth for both surfaces — one plane, one fallback
matrix (probe evidence: `docs/research/2026-08-31-anchored-comments-probe/`,
files 14, 18, 32, 41).

**This is a Developer Preview surface.** Enrollment rides a registered account
plus Cloud project, mise is publicly distributed, and DPP program term (iv) bars
shipping pre-GA features to customers — so an unenrolled caller must get exactly
today's behaviour. Every failure here therefore degrades to `AnchorRead(None,
reason)` and the caller renders comments.md flat with a cue naming why. Nothing
in this module may raise into a fetch.

No `@with_retry`: this is optional enrichment on a preview endpoint, and a
non-enrolled caller's 400/403 is a *settled* answer — retrying it three times
buys nothing and triples the latency of every fetch that will never get
locators.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from adapters.http_client import get_sync_client

_SLIDES_API = "https://slides.googleapis.com/v1/presentations"
_SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"

# The preview opt-in. Both surfaces honour it alone — unlike Docs, which also
# demands an explicit suggestionsViewMode and includeTabsContent (evidence 06/15).
_COMMENTS_VIEW_MODE = "COMMENTS_VIEW_MODE_INCLUDED"

# Ask for the anchor map and nothing else. `comments` carries commentId →
# anchorId (+ plainTextQuote, the anchored text as a string — the only anchor
# context these two surfaces have ever exposed); the per-page / per-tab
# `commentAnchors` lists resolve anchorId → the object it sits on.
_SLIDES_ANCHOR_FIELDS = (
    "presentationId,"
    "comments(commentId,anchorId,plainTextQuote),"
    "slides(objectId,commentAnchors)"
)
_SHEETS_ANCHOR_FIELDS = (
    "spreadsheetId,"
    "comments(commentId,anchorId,plainTextQuote),"
    "sheets(properties(sheetId,title),commentAnchors)"
)


@dataclass
class AnchorRead:
    """A preview anchor read, or the reason there isn't one.

    Exactly one of the two is set. `reason` is written to be pasted into a
    user-facing cue: it names the surface, the failure and, where the status
    implies it, that enrollment is the likely cause.
    """

    payload: dict[str, Any] | None = None
    reason: str | None = None


def _preview_get(url: str, fields: str, surface: str, container: str) -> AnchorRead:
    """GET a preview anchor map, converting every failure into a reason string.

    `container` is the key that must be present for the payload to be a usable
    map (`slides` / `sheets`). A 200 that omits it is a *silent* degradation —
    every comment would come back unlocated with nothing to cue — so it is
    converted into a reason like any refusal. A real deck always has slides, so
    this fires only if Google changes the surface, which is the failure this
    preview dependency is most likely to meet before GA.
    """
    try:
        payload = get_sync_client().get_json(
            url,
            params={"commentsViewMode": _COMMENTS_VIEW_MODE, "fields": fields},
        )
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        detail = (
            " — the anchored-comments Developer Preview is granted per registered "
            "account and Cloud project, so an unenrolled caller sees this"
            if status in (400, 403, 404)
            else ""
        )
        return AnchorRead(
            reason=f"the {surface} anchored-comments preview read returned "
                   f"HTTP {status}{detail}"
        )
    except Exception as e:  # noqa: BLE001 — a preview read may never kill a fetch
        return AnchorRead(
            reason=f"the {surface} anchored-comments preview read failed: "
                   f"{type(e).__name__}: {e}"
        )

    if not isinstance(payload, dict):
        return AnchorRead(
            reason=f"the {surface} anchored-comments preview read returned "
                   f"{type(payload).__name__}, not an object"
        )
    if not payload.get(container):
        return AnchorRead(
            reason=f"the {surface} anchored-comments preview read returned no "
                   f"`{container}` to resolve anchors against"
        )
    return AnchorRead(payload=payload)


def fetch_slides_comment_anchors(presentation_id: str) -> AnchorRead:
    """Read the deck's anchorId → slide/shape map (Slides API, preview)."""
    return _preview_get(
        f"{_SLIDES_API}/{presentation_id}", _SLIDES_ANCHOR_FIELDS, "Slides", "slides"
    )


def fetch_sheets_comment_anchors(spreadsheet_id: str) -> AnchorRead:
    """Read the workbook's anchorId → tab/GridRange map (Sheets API, preview)."""
    return _preview_get(
        f"{_SHEETS_API}/{spreadsheet_id}", _SHEETS_ANCHOR_FIELDS, "Sheets", "sheets"
    )
