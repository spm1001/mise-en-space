"""
Anchored comment writes — Docs, Slides, Sheets batchUpdate (mise-jupuja).

The write twin of `adapters/comment_anchors.py`. Where that module READS the
anchor map so `comments.md` can say where a comment sits, this one CREATES a
comment already sitting somewhere: `insertComment` on each surface's batchUpdate
plane, which is the only API that can write an anchor at all (the Drive comments
plane can post a thread but not attach it to anything — the mise-mikawi report:
those land panel-only, labelled "Original content deleted").

Three facts measured on 2026-09-01 shape everything here
(`docs/research/2026-09-01-jupuja-anchored-write/`):

- **The Docs range is read in the `SUGGESTIONS_INLINE` index space.** Resolve a
  quote against the clean view and the comment anchors to different text under a
  200. `read_document_for_anchoring` therefore hard-codes the view mode rather
  than taking it as a parameter — it is not a caller's choice to get wrong.
- **`writeControl.requiredRevisionId` guards Docs and Slides, and is silently
  ignored by Sheets** (a bogus id returned 200). So the anti-race pin exists on
  two surfaces and cannot be faked on the third.
- **A comment insert bumps the document revision.** A pin is only good for the
  moment it was read, which is why the caller re-resolves and retries rather
  than treating a mismatch as failure.

Preview surface: these endpoints are Developer Preview. Unlike the read side,
a refusal here must NOT degrade to the unanchored plane — a comment that lands
somewhere other than where it was aimed reads as authored intent. Failures
propagate as MiseError for the tools layer to turn into a loud refusal.
"""

from typing import Any

import httpx

from adapters.http_client import get_sync_client
from models import ErrorKind, MiseError

_DOCS_API = "https://docs.googleapis.com/v1/documents"
_SLIDES_API = "https://slides.googleapis.com/v1/presentations"
_SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"

# The one legal value when resolving an anchor range. Measured, not chosen.
_INLINE = "SUGGESTIONS_INLINE"

# `tabs` whole, deliberately unmasked below the top level. Two lessons paid for
# in one day: asking for `tabs` and `body` together is rejected outright ("Field
# mask may not contain legacy text-level Document resource fields while
# requesting tabs content" — only Google can judge a mask, so no stubbed test
# sees it), and every narrowing of the sub-mask hid content the resolver needed
# — `paragraph(...)` alone hid text inside TABLES, producing false uniqueness,
# and omitting `childTabs` hid NESTED tabs from the multi-tab guard. The bytes
# are worth it: a mask that hides content makes the anchor land somewhere else.
_DOC_ANCHOR_FIELDS = "documentId,revisionId,tabs"


def _wrap(e: httpx.HTTPStatusError, what: str) -> MiseError:
    """Google's own message is the diagnostic — carry it, don't paraphrase."""
    status = e.response.status_code
    try:
        detail = (e.response.json().get("error") or {}).get("message") or ""
    except Exception:  # noqa: BLE001 — a non-JSON error body is still an error
        detail = e.response.text[:200]
    kind = {
        400: ErrorKind.INVALID_INPUT,
        403: ErrorKind.PERMISSION_DENIED,
        404: ErrorKind.NOT_FOUND,
        429: ErrorKind.RATE_LIMITED,
    }.get(status, ErrorKind.NETWORK_ERROR)
    return MiseError(
        kind, f"{what}: HTTP {status}{' — ' + detail if detail else ''}",
        details={"http_status": status, "google_message": detail},
        retryable=status in (429, 500, 502, 503),
    )


def read_document_for_anchoring(document_id: str) -> dict[str, Any]:
    """Document structure + revisionId, in the index space the write will use."""
    try:
        return get_sync_client().get_json(
            f"{_DOCS_API}/{document_id}",
            params={"suggestionsViewMode": _INLINE, "includeTabsContent": "true",
                    "fields": _DOC_ANCHOR_FIELDS},
        )
    except httpx.HTTPStatusError as e:
        raise _wrap(e, "reading the document to resolve the anchor") from e


def read_presentation_slides(presentation_id: str) -> dict[str, Any]:
    """Slide object ids in deck order, plus revisionId."""
    try:
        return get_sync_client().get_json(
            f"{_SLIDES_API}/{presentation_id}",
            params={"fields": "presentationId,revisionId,slides(objectId)"},
        )
    except httpx.HTTPStatusError as e:
        raise _wrap(e, "reading the deck to resolve the anchor") from e


def read_spreadsheet_tabs(spreadsheet_id: str) -> dict[str, Any]:
    """Tab titles and numeric sheet ids. No revisionId — Sheets has none here."""
    try:
        return get_sync_client().get_json(
            f"{_SHEETS_API}/{spreadsheet_id}",
            params={"fields": "spreadsheetId,sheets(properties(sheetId,title,"
                              "gridProperties(rowCount,columnCount)))"},
        )
    except httpx.HTTPStatusError as e:
        raise _wrap(e, "reading the workbook to resolve the anchor") from e


def _insert(url: str, request: dict[str, Any], revision: str | None) -> dict[str, Any]:
    """POST exactly ONE insertComment, optionally pinned to a revision.

    One request per batch is a rule, not a simplification: a batch mixing
    comment and content requests can report `commentUpdateState:
    ALL_FAILED_UNKNOWN_REASON` while its content changes commit (picihi), and a
    comment write must never be able to alter the document it comments on.
    """
    body: dict[str, Any] = {"requests": [{"insertComment": request}]}
    if revision:
        body["writeControl"] = {"requiredRevisionId": revision}
    try:
        return get_sync_client().post_json(url, json_body=body)
    except httpx.HTTPStatusError as e:
        raise _wrap(e, "creating the anchored comment") from e
    except Exception as e:  # noqa: BLE001 — the AMBIGUOUS failure, named as such
        # A timeout or dropped connection says nothing about whether Google
        # committed the write. A caller who reads "failed" and retries posts the
        # comment twice. The 200-with-no-thread case already warned about this;
        # the no-response case is worse and had no warning at all.
        raise MiseError(
            ErrorKind.NETWORK_ERROR,
            f"the comment write did not complete cleanly ({type(e).__name__}: {e}) "
            "— it is NOT known whether the comment was created. Check the file "
            "before retrying; a blind retry would post it twice.",
            details={"ambiguous_write": True},
        ) from e


def insert_doc_comment(
    document_id: str, content: str, start: int, end: int,
    *, assignee: str | None = None, revision: str | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {"content": content, "range": {"startIndex": start, "endIndex": end}}
    if assignee:
        request["assigneeEmailAddress"] = assignee
    return _insert(f"{_DOCS_API}/{document_id}:batchUpdate", request, revision)


def insert_slide_comment(
    presentation_id: str, content: str, object_id: str,
    *, assignee: str | None = None, revision: str | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {"content": content, "objectId": object_id}
    if assignee:
        request["assigneeEmailAddress"] = assignee
    return _insert(f"{_SLIDES_API}/{presentation_id}:batchUpdate", request, revision)


def insert_cell_comment(
    spreadsheet_id: str, content: str, sheet_id: int, row: int, column: int,
    *, assignee: str | None = None,
) -> dict[str, Any]:
    """No `revision` parameter, deliberately: Sheets accepts `writeControl` and
    ignores it (measured), so offering one would be a guard that guards nothing.
    """
    request: dict[str, Any] = {
        "content": content,
        "coordinate": {"sheetId": sheet_id, "rowIndex": row, "columnIndex": column},
    }
    if assignee:
        request["assigneeEmailAddress"] = assignee
    return _insert(f"{_SHEETS_API}/{spreadsheet_id}:batchUpdate", request, None)


def thread_from_reply(response: dict[str, Any]) -> dict[str, Any] | None:
    """The created thread out of a batchUpdate response, or None.

    None is a real answer, not a parse failure to shrug at: a 200 whose reply
    carries no thread means the write reported success without producing the
    comment, and the caller must say so rather than invent an id.
    """
    replies = response.get("replies")
    if not isinstance(replies, list) or not replies:
        return None
    first = replies[0]
    if not isinstance(first, dict):
        return None
    thread = (first.get("insertComment") or {}).get("commentThread")
    return thread if isinstance(thread, dict) else None
