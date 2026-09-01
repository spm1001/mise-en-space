"""
Suggested edits — propose instead of impose, and fold the proposals back.

Two halves of one loop (mise-hupago).

**Proposing.** `suggest=True` on the Docs surgical ops sends the same
batchUpdate with `writeControl.writeMode=SUGGEST`, so the edit arrives as a
tracked change awaiting a human's accept. This is the mise-wagina philosophy —
the human's yes is a UI event — reaching the editing plane: for contentious
edits, propose-don't-impose replaces restore-point-as-apology.

**Folding.** `do(suggest, action='accept'|'reject', find='s2')` resolves a
suggestion by the `[sN]` tag a `suggestions='markup'` fetch prints, or by a raw
`suggest.…` id.

Three things bite, and all three are the same shape: **a suggest batch can
report success while having done nothing.**

1. **`[sN]` is an ORDINAL, not an id.** `extractors/docs.annotate_suggestion_markup`
   numbers suggestions in first-appearance order for rendering. Accept `s1` and
   every later tag RENUMBERS, so a caller folding `s1` then `s2` folds the wrong
   thing second. Ordinals are therefore resolved fresh on every call, and
   folding several at once is refused.
2. **A find string that matches nothing returns HTTP 200.** `occurrencesChanged:
   0`, `suggestionResponses: [{}]`, `commentUpdateState: NO_UPDATES_REQUESTED`.
   Under a direct edit that is a cue; under `suggest=` it is a trap, because the
   caller now believes a change is sitting in the document awaiting review when
   nothing exists at all. It is raised as an error.
3. **Word-imported documents are where 2 actually happens.** Google's `.docx`
   converter can join words with NBSP (U+00A0), so an ordinary-space find string
   silently matches nothing — measured live on 2026-09-01, an imported paragraph
   took 0 occurrences with spaces and 1 with NBSPs. When a suggested replace
   finds nothing, mise checks the NBSP spelling and says so rather than leaving
   the caller to wonder.

A refused SUGGEST is NEVER downgraded to a direct edit. That inversion — the
human believing an edit is pending approval when it has already landed — is the
exact opposite of what this feature is for.
"""

from typing import Any

from adapters.docs import fetch_document
from adapters.http_client import get_sync_client
# _iter_suggestion_runs is private, and reused anyway: it already walks tabs,
# paragraphs and table cells in reading order, and re-implementing that walk is
# precisely the mistake mise-jupuja's handoff wrote up (a lesson banked in one
# module not reaching its sibling). extractors/docs.py is at its size ceiling,
# so a public alias there would cost a line it does not have.
from extractors.docs import (
    _iter_paragraphs,
    _iter_suggestion_runs,
    annotate_suggestion_markup,
)
from models import DoResult, ErrorKind, MiseError
from validation import validate_drive_id

import re

_DOCS_API = "https://docs.googleapis.com/v1/documents"
_ORDINAL = re.compile(r"[sS]\d+")
NBSP = " "

# commentUpdateState values that mean "the batch did what it said". Anything
# else — ALL_FAILED_UNKNOWN_REASON above all — can coexist with committed model
# changes, which is the documented silent-partial hazard (mise-picihi).
_STATES_OK = {"ALL_SAVED", "NO_UPDATES_REQUESTED", ""}

_DIRECT = (
    "Drop suggest=True to make the edit directly (it is reversible — Doc edits "
    "return cues.restore_point naming the Version history entry)."
)


def suggest_write_control() -> dict[str, Any]:
    """The one flag that turns a Docs batchUpdate into tracked changes."""
    return {"writeMode": "SUGGEST"}


def created_ids(response: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for entry in response.get("suggestionResponses") or []:
        if isinstance(entry, dict):
            out.extend(entry.get("createdSuggestionIds") or [])
    return out


def check_batch_state(response: dict[str, Any]) -> None:
    """Raise when the batch's own status line says it did not save.

    Read on EVERY suggest batch. `ALL_FAILED_UNKNOWN_REASON` is documented to
    coexist with committed model changes, so treating a 200 as success is the
    silent-partial hazard rather than an edge case.
    """
    state = response.get("commentUpdateState", "")
    if state not in _STATES_OK:
        raise MiseError(
            ErrorKind.EXTRACTION_FAILED,
            f"the document reported commentUpdateState={state!r} — the "
            "suggestion may not have saved, and the document may still have "
            "changed. Re-read the document before retrying.",
            details={"comment_update_state": state},
        )


def nbsp_hint(file_id: str, find: str) -> str:
    """A sentence naming the NBSP trap, when that is really what happened.

    Only called after a suggested replace has already found nothing, so the
    extra read is paid on the failure path alone. Returns "" when NBSPs are not
    the explanation — guessing at a cause is worse than not naming one.
    """
    if " " not in find:
        return ""
    try:
        doc = get_sync_client().get_json(
            f"{_DOCS_API}/{file_id}",
            params={"suggestionsViewMode": "SUGGESTIONS_INLINE",
                    "includeTabsContent": "true", "fields": "tabs"},
        )
    except Exception:  # noqa: BLE001 — a hint is optional; its absence is not an error
        return ""
    text = _all_text(doc)
    if find.replace(" ", NBSP) in text:
        return (
            " The document DOES contain that text with non-breaking spaces "
            "(U+00A0) between the words — the signature of a document imported "
            "from Word. Retry with NBSPs in find=, or pick a single word."
        )
    return ""


def _all_text(doc: dict[str, Any]) -> str:
    """Every character of body text, INCLUDING inside tables.

    The first version walked top-level paragraphs only, so the NBSP diagnosis
    stayed silent on a Word-imported table — the shape most likely to carry the
    trap. `_iter_paragraphs` already recurses into table cells, and hand-rolling
    that walk beside it is the mistake mise-jupuja's handoff wrote up; this is
    the same import, used properly (essayeur, mise-hupago).
    """
    parts: list[str] = []

    def walk(tabs: Any) -> None:
        for tab in tabs or []:
            if not isinstance(tab, dict):
                continue
            body = (tab.get("documentTab") or {}).get("body") or {}
            for para in _iter_paragraphs(body.get("content") or []):
                for run in para.get("elements") or []:
                    parts.append(((run.get("textRun") or {}).get("content") or ""))
            walk(tab.get("childTabs"))

    walk(doc.get("tabs"))
    return "".join(parts)


# Suggestion kinds mise can TAG and fold by ordinal are text insertions and
# deletions. Google also holds style-only suggestions — a Word `w:rPrChange`
# converts to `suggestedTextStyleChanges` — which carry no inserted or deleted
# run, so they cannot be rendered as CriticMarkup and cannot be given an [sN].
# They are still real pending suggestions in the document. Counting only what
# we can render made mise state a total that was simply wrong: a style-only
# import reported "0 unresolved suggestions" about a document Google held one
# for (essayeur, mise-hupago). So they are counted separately and DISCLOSED.
_STYLE_SUGGESTION_FIELDS = (
    "suggestedTextStyleChanges",
    "suggestedParagraphStyleChanges",
    "suggestedBulletChanges",
)


def count_untaggable_suggestions(tabs: Any) -> int:
    """Pending suggestions mise cannot tag — formatting rather than text."""
    ids: set[str] = set()
    for tab in tabs:
        for para in _iter_paragraphs(tab.body.get("content", [])):
            for field in _STYLE_SUGGESTION_FIELDS:
                ids.update((para.get(field) or {}).keys())
            for element in para.get("elements") or []:
                run = element.get("textRun")
                if not isinstance(run, dict):
                    continue
                for field in _STYLE_SUGGESTION_FIELDS:
                    ids.update((run.get(field) or {}).keys())
    return len(ids)


def untaggable_note(count: int) -> str:
    """The sentence that keeps a text-suggestion count from reading as a total."""
    if not count:
        return ""
    return (
        f" This document also holds {count} FORMATTING suggestion(s) (bold, "
        "style, bullets) that mise cannot tag or fold — they are real and "
        "pending; accept or reject those in the Docs UI."
    )


def resolve_suggestion_id(file_id: str, wanted: str) -> tuple[str, int]:
    """`'s2'` → the real `suggest.…` id, plus the document's total count.

    Resolved fresh from the document every time, because the ordinal is a
    property of the current rendering: fold one suggestion and the rest
    renumber. A raw `suggest.…` id is passed through untouched — it is stable,
    and a caller who has one should not be forced through the ordinal.
    """
    # Native Docs mint `suggest.<hash>`; a document CONVERTED FROM .docx mints
    # `suggestIdImport<uuid>_N` instead (measured 2026-09-01 on a real Word
    # import). Matching only the native spelling refused a perfectly good id on
    # exactly the documents this card's falsifier is about, so the passthrough
    # tests the family, and the ordinal is the narrow shape.
    if _ORDINAL.fullmatch(wanted.strip()):
        tag = wanted.strip().lower()
    elif wanted.startswith("suggest"):
        return (wanted, 0)
    else:
        raise ValueError(
            f"{wanted!r} is not a suggestion reference — use the [sN] tag from a "
            "suggestions='markup' fetch (e.g. 's2'), or a raw suggest.… id"
        )

    doc = fetch_document(file_id, suggestions="markup")
    annotate_suggestion_markup(doc.tabs)
    seen: dict[str, str] = {}
    for run in _iter_suggestion_runs(doc.tabs):
        marker = run.get("_mise_suggestion_tag")
        if not marker or marker in seen:
            continue
        ids = (run.get("suggestedDeletionIds") or []) or (
            run.get("suggestedInsertionIds") or [])
        if ids:
            seen[marker] = ids[0]
    total = len(seen)
    if tag not in seen:
        raise ValueError(
            f"this document has {total} TEXT suggestion(s)"
            + (f" ({', '.join(sorted(seen))})" if seen else "")
            + f", so {wanted!r} does not exist. Fetch it with suggestions='markup' "
            "to see the current tags — they RENUMBER each time one is folded."
            + untaggable_note(count_untaggable_suggestions(doc.tabs))
        )
    return (seen[tag], total)


def do_suggest(
    file_id: str | None = None,
    action: str | None = None,
    find: str | None = None,
) -> DoResult | dict[str, Any]:
    """
    Accept or reject a suggested edit on a Google Doc.

    Args:
        file_id: The document
        action: 'accept' or 'reject'
        find: which suggestion — the `[sN]` tag from a suggestions='markup'
            fetch, or a raw `suggest.…` id

    Returns:
        DoResult on success, error dict on failure
    """
    if not file_id:
        return _bad("suggest requires 'file_id'")
    if action not in ("accept", "reject"):
        return _bad("suggest requires action='accept' or action='reject'")
    if not find:
        return _bad(
            "suggest requires find= naming the suggestion — the [sN] tag from a "
            "fetch with suggestions='markup', or a raw suggest.… id"
        )
    try:
        validate_drive_id(file_id, "file_id")
    except ValueError as e:
        return _bad(str(e))

    wanted = find.strip()
    if "," in wanted or " " in wanted:
        # Folding several at once cannot be done safely through ordinals: the
        # first accept renumbers the rest, so the second id would name a
        # different suggestion from the one the caller read.
        return _bad(
            "fold one suggestion per call. [sN] tags RENUMBER after each fold, "
            "so a list would name the wrong suggestions after the first — "
            "re-fetch with suggestions='markup' between calls."
        )

    try:
        suggestion_id, total = resolve_suggestion_id(file_id, wanted)
    except ValueError as e:
        return _bad(str(e))
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    request = ("acceptSuggestion" if action == "accept" else "rejectSuggestion")
    try:
        response = get_sync_client().post_json(
            f"{_DOCS_API}/{file_id}:batchUpdate",
            json_body={"requests": [{request: {"suggestionId": suggestion_id}}]},
        )
        check_batch_state(response)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}
    except Exception as e:  # noqa: BLE001 — ambiguous outcome, named as such
        return {"error": True, "kind": "network_error",
                "message": f"the fold did not complete cleanly ({type(e).__name__}: {e}) "
                           "— it is NOT known whether the suggestion was resolved. "
                           "Re-fetch with suggestions='markup' before retrying."}

    # The API's own report of what it resolved — an empty list means the request
    # was accepted and did nothing, which a 200 alone cannot tell you.
    key = "acceptedSuggestionIds" if action == "accept" else "rejectedSuggestionIds"
    resolved = [
        i for entry in (response.get("suggestionResponses") or [])
        if isinstance(entry, dict) for i in (entry.get(key) or [])
    ]
    cues: dict[str, Any] = {
        "action": f"{action}ed suggestion {wanted} ({suggestion_id})",
        "suggestion_id": suggestion_id,
        "resolved": bool(resolved),
    }
    if not resolved:
        cues["warning"] = (
            f"The API returned no {key} — the suggestion may not have been "
            f"{action}ed. Re-fetch with suggestions='markup' to check."
        )
    if total > 1:
        cues["renumbered"] = (
            f"{total - 1} TEXT suggestion(s) remain and their [sN] tags have "
            "RENUMBERED. Re-fetch with suggestions='markup' before folding another."
        )
    return DoResult(
        file_id=file_id, title=f"Suggestion {wanted}",
        web_link=f"https://docs.google.com/document/d/{file_id}/edit",
        operation="suggest", cues=cues,
    )


def _bad(message: str) -> dict[str, Any]:
    return {"error": True, "kind": "invalid_input", "message": message}


def _no_suggest_here(mime: str, op: str) -> dict[str, Any]:
    """Refuse suggest= off the Docs plane rather than ignoring it.

    Only Google Docs has a tracked-changes model. Sheets and Slides edits, and
    plain-file writes, go through engines with no SUGGEST mode — so accepting
    the flag and dropping it would land a REAL edit while the caller believes a
    proposal is waiting for a human. That inversion is the one thing this
    feature exists to prevent, which makes silence the worst possible answer.
    """
    return {
        "error": True, "kind": "invalid_input",
        "message": (
            f"suggest=True works on Google Docs only — this file is "
            f"{mime or 'not a Google Doc'}, and {op} there has no tracked-changes "
            f"mode. Nothing was written. {_DIRECT}"
        ),
    }


def _suggest_cues(response: dict[str, Any], op: str) -> dict[str, Any]:
    """Cues for a suggested edit, and the checks that make them honest.

    A suggest batch reports 200 whether or not it created anything, so the
    caller is told what the API says it did — the created ids — rather than what
    the request asked for. `commentUpdateState` is read on every batch: it can
    report failure while model changes commit, which is the documented silent-
    partial hazard.
    """
    check_batch_state(response)
    ids = created_ids(response)
    cues: dict[str, Any] = {
        "suggested": True,
        "accept_with": "do(suggest, file_id=…, action='accept', find='sN')",
    }
    if ids:
        cues["suggestion_ids"] = ids
        cues["coalescing"] = (
            "Adjacent suggestions by the same author COALESCE into one thread — "
            "accepting one can accept a neighbour. Granularity is Google's; "
            "re-fetch with suggestions='markup' to see the real grouping."
        )
        return cues
    # NO created id, and the edit still landed. Measured live 2026-09-01: an
    # edit touching text already inside a pending suggestion is absorbed into
    # THAT thread, so Google reports no new id — the coalescing the card warns
    # about, seen from the response side. An earlier version of this function
    # raised here, which was the inverse error and worse than the one it was
    # guarding: the caller is told nothing was created while a tracked change
    # sits in the document, and a retry double-applies. The genuine
    # nothing-happened cases are caught upstream (zero occurrences) and by
    # check_batch_state; an empty id list on a landed edit is not one of them.
    cues["coalesced"] = (
        "No NEW suggestion id came back: this edit was absorbed into a "
        "suggestion that already covered that text. The change IS pending "
        "review — re-fetch with suggestions='markup' to see which [sN] thread "
        "now carries it, and note that accepting it will accept the rest of "
        "that thread too."
    )
    return cues
