"""
Comment operation — open a NEW comment thread on a Drive file via do().

Two planes, chosen by whether the caller said WHERE.

- **`anchor=` given** → the surface's own batchUpdate `insertComment`, which is
  the only API that can attach a thread to something: a slide, a cell, a run of
  text. This is the fix route for mise-mikawi.
- **`anchor=` omitted** → the Drive comments plane, unchanged, plus a cue saying
  what that costs. An unanchored comment renders NOWHERE in the document: Google
  treats anchorless as anchor-rotted and files it in the comments panel under
  "Original content deleted" (confirmed by screenshot, picihi evidence 42). The
  op still does what it always did; the caller now knows what they are getting.

Agent-authored content is auto-prefixed '[agent] ' so humans can tell agent
comments from their own — Sameer's convention, matching tools/comment_reply.py.

**A refused anchor is never quietly downgraded to unanchored.** The anchored
plane is Developer Preview and can refuse an unenrolled caller outright, but a
comment that lands somewhere other than where it was aimed reads as authored
intent — worse than no comment. So every failure to place a thread exactly is a
loud refusal naming the unanchored fallback, which is the opposite of the READ
side's degrade-with-a-cue (mise-dukacu) and deliberately so.
"""

import re
from typing import Any

from adapters.anchored_comments import (
    insert_cell_comment,
    insert_doc_comment,
    insert_slide_comment,
    read_document_for_anchoring,
    read_presentation_slides,
    read_spreadsheet_tabs,
    thread_from_reply,
)
from adapters.drive import (
    COMMENT_UNSUPPORTED_MIMES,
    create_comment,
    get_file_metadata,
)
from extractors.comment_anchors import (  # noqa: F401
    _tab_bodies,
    locate_quote,
    parse_a1_cell,
    parse_slide_spec,
    suggested_spans,
)
from models import DoResult, MiseError
from validation import validate_drive_id

_AGENT_PREFIX = "[agent] "
_DOC_MIME = "application/vnd.google-apps.document"
_DECK_MIME = "application/vnd.google-apps.presentation"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"

# A pinned revision can go stale between reading and writing — a comment insert
# alone bumps it. The caller's intent is the anchor they named, not the indices
# we derived, so re-resolve and try again rather than failing.
_REVISION_RETRIES = 2
_STALE = "does not match the latest revision"

_FALLBACK = (
    "Drop anchor= to post an unanchored comment instead — it will be visible "
    "only in the comments panel, not on the content."
)


def _refuse(message: str, kind: str = "invalid_input") -> dict[str, Any]:
    return {"error": True, "kind": kind, "message": message}


def _norm(text: str) -> str:
    """Whitespace-insensitive comparison — the same normalisation the resolver
    matched with, so the landing check cannot fail on a line break."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def _overlaps_suggestion(doc: dict[str, Any], start: int, end: int) -> bool:
    return any(s < end and start < e for s, e in suggested_spans(doc))


def _assignee_cues(cues: dict[str, Any], assignee: str | None) -> None:
    """Assignment is stored whether or not the person can reach the file.

    Measured 2026-09-01: an address with no access to the document was accepted
    and stored as the assignee, with no error and no notification path. A 200
    therefore cannot be read as "they will see it", so mise says so every time.
    """
    if not assignee:
        return
    cues["assignee"] = assignee
    cues["assignee_warning"] = (
        f"Assigned to {assignee}. Google does NOT check that an assignee can "
        "open the file — if they lack access the assignment is stored and they "
        "are never told. Confirm they are a collaborator."
    )


def _slide_object_id(spec: str, deck: dict[str, Any]) -> tuple[str, str]:
    """(objectId, human label) for a slide spec, or ValueError naming the deck."""
    slides = [s for s in (deck.get("slides") or []) if isinstance(s, dict)]
    kind, value = parse_slide_spec(spec)
    if kind == "index":
        assert isinstance(value, int)
        if value >= len(slides):
            raise ValueError(
                f"this deck has {len(slides)} slide(s), so slide {value + 1} "
                "does not exist"
            )
        return (slides[value].get("objectId") or "", f"slide {value + 1}")
    known = {s.get("objectId") for s in slides}
    if value not in known:
        raise ValueError(
            f"no slide with object id {value!r} in this deck — use "
            "anchor='slide N' (the numbering comments.md prints)"
        )
    position = next(i for i, s in enumerate(slides) if s.get("objectId") == value)
    return (str(value), f"slide {position + 1}")


def _cell_target(spec: str, book: dict[str, Any]) -> tuple[int, int, int, str]:
    """(sheetId, row0, col0, label) for an A1 spec, or ValueError naming tabs."""
    tabs = [s for s in (book.get("sheets") or []) if isinstance(s, dict)]
    if not tabs:
        raise ValueError("this workbook reports no tabs")
    tab_name, row, col = parse_a1_cell(spec)

    if tab_name is None:
        chosen = tabs[0]
    else:
        matches = [t for t in tabs
                   if ((t.get("properties") or {}).get("title") or "").strip().lower()
                   == tab_name.strip().lower()]
        if not matches:
            names = ", ".join(
                repr((t.get("properties") or {}).get("title")) for t in tabs
            )
            raise ValueError(f"no tab named {tab_name!r} — this workbook has {names}")
        chosen = matches[0]

    props = chosen.get("properties") or {}
    grid = props.get("gridProperties") or {}
    rows, cols = grid.get("rowCount"), grid.get("columnCount")
    title = props.get("title") or "sheet"
    if rows is not None and row >= rows:
        raise ValueError(f"{title!r} has {rows} rows, so row {row + 1} is off the grid")
    if cols is not None and col >= cols:
        raise ValueError(f"{title!r} has {cols} columns, so that column is off the grid")
    return (int(props.get("sheetId", 0)), row, col, f"{title}!{spec.split('!')[-1].strip()}")


def _doc_range(spec: str, doc: dict[str, Any]) -> tuple[int, int]:
    """(start, end) for a quoted-text anchor, or ValueError saying which way it failed.

    Zero matches and several matches are different refusals on purpose. Several
    is the dangerous one: picking the first occurrence would place the comment
    on text the caller did not mean, and they would have no way to notice.
    """
    # Tabs nest, so count them the way _tab_bodies walks them — a document with
    # one root tab and children is a multi-tab document.
    tab_count = len(_tab_bodies(doc)) if doc.get("tabs") else 1
    if tab_count > 1:
        raise ValueError(
            f"this document has {tab_count} tabs, and anchoring across tabs is "
            "not yet measured — mise refuses rather than risk anchoring in the "
            "wrong tab (mise-jupuja)"
        )
    hits = locate_quote(doc, spec)
    if not hits:
        raise ValueError(
            f"the text {spec[:60]!r} was not found in this document's body — "
            "anchor= must quote text that exists (whitespace is forgiven). Text "
            "in headers, footers, footnotes and speaker notes is NOT searched "
            "and cannot be anchored to"
        )
    if len(hits) > 1:
        raise ValueError(
            f"the text {spec[:60]!r} appears {len(hits)} times — quote a longer, "
            "unique passage so the comment cannot land on the wrong one"
        )
    return hits[0]


def _anchored(
    file_id: str, content: str, anchor: str, mime: str, assignee: str | None
) -> DoResult | dict[str, Any]:
    """Resolve the anchor and create the thread, re-resolving on a stale pin."""
    cues: dict[str, Any] = {}
    attempt = 0
    # A Docs anchor is content-addressed (a quote), so re-resolving it after a
    # concurrent edit re-finds the caller's intent. A slide anchor is
    # POSITION-addressed: re-resolving 'slide 2' against a changed deck aims at
    # a slide the caller never saw. So the deck's answer is pinned on the first
    # pass and reused, and only the revision is refreshed (essayeur, jupuja).
    pinned_slide: str | None = None
    doc_payload: dict[str, Any] | None = None
    start = end = 0
    while True:
        try:
            if mime == _DECK_MIME:
                deck = read_presentation_slides(file_id)
                if pinned_slide is None:
                    pinned_slide, label = _slide_object_id(anchor, deck)
                elif pinned_slide not in {
                    s.get("objectId") for s in (deck.get("slides") or [])
                }:
                    # The slide the caller aimed at is gone. Re-resolving the
                    # POSITION would silently hit whatever slid into its place.
                    return _refuse(
                        f"the slide you anchored to ({anchor!r}) was deleted "
                        f"while the comment was being placed — nothing was "
                        f"written. {_FALLBACK}", kind="conflict")
                object_id = pinned_slide
                response = insert_slide_comment(
                    file_id, content, object_id,
                    assignee=assignee, revision=deck.get("revisionId"),
                )
            elif mime == _SHEET_MIME:
                book = read_spreadsheet_tabs(file_id)
                sheet_id, row, col, label = _cell_target(anchor, book)
                response = insert_cell_comment(
                    file_id, content, sheet_id, row, col, assignee=assignee
                )
                cues["race_note"] = (
                    "Sheets has no revision guard for comment writes (measured: "
                    "writeControl is accepted and ignored), so a row or column "
                    "inserted while this ran could shift the target — check the "
                    "quoted text below."
                )
            else:
                doc = doc_payload = read_document_for_anchoring(file_id)
                start, end = _doc_range(anchor, doc)
                label = f"the text {anchor[:40]!r}"
                response = insert_doc_comment(
                    file_id, content, start, end,
                    assignee=assignee, revision=doc.get("revisionId"),
                )
            break
        except ValueError as e:
            return _refuse(f"Could not anchor the comment: {e}. {_FALLBACK}")
        except MiseError as e:
            stale = _STALE in str(e.details.get("google_message", ""))
            if stale and attempt < _REVISION_RETRIES:
                attempt += 1
                continue
            if stale:
                return _refuse(
                    "The file changed while the comment was being placed, "
                    f"{_REVISION_RETRIES + 1} times running — nothing was "
                    f"written. Try again when the edits settle. {_FALLBACK}",
                    kind="conflict",
                )
            return _refuse(f"{e.message} {_FALLBACK}", kind=e.kind.value)

    thread = thread_from_reply(response)
    if thread is None:
        return _refuse(
            "The API accepted the write but returned no comment thread, so "
            "mise cannot confirm the comment exists or where it landed. Check "
            "the file before retrying — a thread may have been created.",
            kind="unknown",
        )

    comment_id = thread.get("commentId")
    cues["action"] = f"Created comment {comment_id} on {label}"
    cues["comment_id"] = comment_id
    cues["anchored_to"] = label
    if attempt:
        cues["retries"] = attempt

    # The API's own report of the text it anchored to. On a Doc that is directly
    # comparable to what the caller asked for, so COMPARE it rather than merely
    # displaying it: this is the one check that catches a mislanding whose cause
    # nobody has thought of yet — an index arithmetic bug, a content shape the
    # resolver cannot see, a future change in how Google reads a range. It runs
    # after the write, so it can only disclose, never prevent; that is still the
    # difference between a wrong comment nobody notices and one the caller is
    # told to delete.
    quote = thread.get("plainTextQuote")
    if quote:
        cues["anchor_text"] = quote
    if mime == _DOC_MIME:
        if not quote:
            cues["landing_unverified"] = (
                "The API returned no quote for this thread, so mise cannot "
                "confirm the comment landed on the text you named."
            )
        elif _norm(quote) != _norm(anchor):
            cues["landing_mismatch"] = (
                f"MISLANDED: you aimed at {anchor[:80]!r} but the comment "
                f"anchored to {quote[:80]!r}. The thread exists — delete "
                f"comment {comment_id} and report this, it is a bug in mise."
            )
        if doc_payload is not None and _overlaps_suggestion(doc_payload, start, end):
            cues["anchor_is_provisional"] = (
                "The text you anchored to is an unaccepted SUGGESTION. If it is "
                "rejected, this comment loses its anchor and becomes a "
                "panel-only thread under 'Original content deleted'."
            )
    _assignee_cues(cues, assignee)
    return DoResult(
        file_id=file_id, title=f"Comment on {file_id}", web_link="",
        operation="comment", cues=cues,
    )


def do_comment(
    file_id: str | None = None,
    content: str | None = None,
    anchor: str | None = None,
    to: str | None = None,
) -> DoResult | dict[str, Any]:
    """
    Create a new comment thread on a Drive file.

    Args:
        file_id: The file to comment on
        content: The comment text
        anchor: Where to attach it — 'slide 3' on a deck, 'Sheet1!B12' on a
            workbook, or the text to quote in a Doc. The deck and cell spellings
            are the ones comments.md prints, so a read locator can be pasted
            straight back. Omit for an unanchored, panel-only comment.
        to: Email address to assign the thread to (anchored comments only).

    Returns:
        DoResult on success, error dict on failure
    """
    if not file_id:
        return _refuse("comment requires 'file_id'")

    # Normalise whitespace-only content to "absent".
    if content is not None:
        content = content.strip() or None
    if not content:
        return _refuse("comment requires 'content' (the comment text)")

    try:
        validate_drive_id(file_id, "file_id")
    except ValueError as e:
        return _refuse(str(e))

    if anchor is not None and not anchor.strip():
        return _refuse(
            "anchor= was given but empty. Omit it entirely for an unanchored, "
            "panel-only comment — a blank anchor is more likely a mistake than "
            "a request for one."
        )
    anchor = anchor.strip() if anchor else None
    assignee = to.strip() if to else None
    if assignee and ("," in assignee or " " in assignee):
        return _refuse(
            "a comment is assigned to ONE person — pass a single address in to="
        )
    if assignee and not anchor:
        return _refuse(
            "assigning needs an anchored comment: the Drive comments plane that "
            "serves unanchored comments has no assignee field. Add anchor= "
            "('slide 3', 'Sheet1!B12', or the text to quote in a Doc)."
        )

    # Agent self-disclosure: prefix so humans can tell agent comments apart.
    if not content.startswith(_AGENT_PREFIX):
        content = _AGENT_PREFIX + content

    if anchor:
        try:
            mime = get_file_metadata(file_id).get("mimeType", "")
        except MiseError as e:
            return _refuse(e.message, kind=e.kind.value)
        if mime not in (_DOC_MIME, _DECK_MIME, _SHEET_MIME):
            # Only offer the unanchored fallback where it actually works. On a
            # folder or a Form the Drive comments plane refuses too, so coaching
            # "drop anchor=" would send the caller into a second refusal.
            commentable = (
                mime not in COMMENT_UNSUPPORTED_MIMES
                and mime != "application/vnd.google-apps.folder"
            )
            return _refuse(
                f"anchored comments exist only on Google Docs, Sheets and "
                f"Slides — this file is {mime or 'of unknown type'}."
                + (f" {_FALLBACK}" if commentable
                   else " This file type takes no comments at all.")
            )
        return _anchored(file_id, content, anchor, mime, assignee)

    try:
        comment = create_comment(file_id, content)
    except MiseError as e:
        return _refuse(e.message, kind=e.kind.value)

    cues: dict[str, Any] = {
        "action": f"Created comment {comment.id}",
        "comment_id": comment.id,
        "anchored": False,
        # mise-mikawi: five comments posted this way during real work were
        # reported as missing. They were not missing — they were panel-only.
        "visibility": (
            "UNANCHORED: this comment does not appear on the content at all. "
            "Google files anchorless comments in the comments panel under "
            "'Original content deleted'. To put it where a reader will see it, "
            "pass anchor= ('slide 3', 'Sheet1!B12', or text to quote in a Doc)."
        ),
    }
    return DoResult(
        file_id=file_id, title=f"Comment on {file_id}", web_link="",
        operation="comment", cues=cues,
    )
