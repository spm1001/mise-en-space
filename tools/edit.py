"""
Surgical edit operations — prepend, append, replace_text on Google Docs and plain files.

Google Docs: Docs API batchUpdate with insertText and replaceAllText.
Plain files: Drive Files API (download → modify → re-upload).
Preserves existing content at other positions.

Routing contract: metadata is pre-fetched at dispatch level (server.py) and
passed via metadata= param. If metadata is None (direct call, not via do()),
we fall through to the Google Doc path for backward compatibility. This avoids
an extra Drive API call per edit — the dispatch fetches once, handlers share it.

Uses httpx via MiseSyncClient (Phase 1 migration).
"""

from typing import Any

from adapters.drive import GOOGLE_DOC_MIME, GOOGLE_SHEET_MIME
from adapters.http_client import get_sync_client
from models import DoResult, MiseError, ErrorKind
from retry import with_retry
from tools.common import NO_MATCH_WARNING, markdown_marker_hint
from tools.doc_control_chars import (
    apply_sanitise_cues,
    find_string_warning,
    sanitise_for_insert,
)
from tools.doc_tabs import add_tab_with_content, get_doc_tabs_meta
from tools.plain_file import plain_prepend, plain_append, plain_replace_text
from tools.restore_point import capture_restore_point, merge_restore_cues
from tools.sheet_edit import sheet_replace_text
from validation import validate_drive_id


# Google Docs API v1 base URL
_DOCS_API = "https://docs.googleapis.com/v1/documents"


def _get_doc_meta(client: Any, file_id: str) -> dict[str, Any]:
    """Fetch document title, first-tab end index + id, and tab titles.

    One GET, read entirely through the TABS view: the API refuses a fields
    mask mixing legacy text-level fields with tabs ("Field mask may not
    contain legacy text-level Document resource fields while requesting
    tabs content", 400 — measured live 2026-08-24, a rule no mocked test
    could see). endIndex comes from the first tab's documentTab.body, and
    the first tab's id is returned so prepend/append can address their
    target EXPLICITLY rather than through the undocumented no-tabId
    default. Tab titles feed the multi-tab aiming disclosures: prepend/
    append write the first tab; replaceAllText with no tabsCriteria
    applies to ALL tabs (essayeur catch 2026-08-24, verified live)."""
    doc = client.get_json(
        f"{_DOCS_API}/{file_id}",
        params={
            "includeTabsContent": "true",
            "fields": (
                "title,tabs(tabProperties,"
                "documentTab(body(content(endIndex))))"
            ),
        },
    )
    tabs = doc.get("tabs", [])
    first = tabs[0] if tabs else {}
    first_body = (
        first.get("documentTab", {}).get("body", {}).get("content", [])
    )
    end_index = first_body[-1].get("endIndex", 1) if first_body else 1
    return {
        "title": doc.get("title", "Untitled"),
        "end_index": end_index,
        "first_tab_id": first.get("tabProperties", {}).get("tabId"),
        "tab_titles": [
            t.get("tabProperties", {}).get("title") for t in tabs
        ],
    }


def _first_tab_location(meta: dict[str, Any], index: int) -> dict[str, Any]:
    """Location addressing the first tab explicitly when its id is known."""
    location: dict[str, Any] = {"index": index}
    if meta.get("first_tab_id"):
        location["tabId"] = meta["first_tab_id"]
    return location


def _multi_tab_insert_warning(meta: dict[str, Any], op: str) -> str | None:
    """Warning for prepend/append on a multi-tab doc — the write lands in
    the first tab only, which is invisible from the 200."""
    titles = meta.get("tab_titles") or []
    if len(titles) <= 1:
        return None
    return (
        f"This doc has {len(titles)} tabs — {op} wrote into the FIRST tab "
        f"({titles[0]!r}) only. To add content as its own tab instead: "
        f"do(append, file_id=…, content=…, tab='Title')."
    )


def do_prepend(
    file_id: str | None = None,
    content: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> DoResult | dict[str, Any]:
    """Insert text at the beginning of a document or plain file."""
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "prepend requires 'file_id'"}
    if not content:
        return {"error": True, "kind": "invalid_input",
                "message": "prepend requires 'content'"}
    try:
        validate_drive_id(file_id, "file_id")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}
    if metadata and metadata.get("mimeType") != GOOGLE_DOC_MIME:
        return plain_prepend(file_id, content, metadata)
    # Google Doc path only: insertText deletes \f and \x00 silently
    # (mise-melaso). Plain files above keep both — their bytes go up
    # untouched — so the transform must sit BELOW that routing.
    content, cc_state = sanitise_for_insert(content)
    restore_cues = capture_restore_point(file_id)
    try:
        result = _prepend(file_id, content)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}
    apply_sanitise_cues(result.cues, cc_state)
    return merge_restore_cues(result, restore_cues)


def do_append(
    file_id: str | None = None,
    content: str | None = None,
    metadata: dict[str, Any] | None = None,
    tab: str | None = None,
) -> DoResult | dict[str, Any]:
    """Insert text at the end of a document or plain file.

    With tab= (Google Docs only): the content becomes a NEW tab of that
    title instead — the non-destructive parallel-version placement
    (mise-wisuzu). Existing tabs are never touched.
    """
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "append requires 'file_id'"}
    if not content:
        return {"error": True, "kind": "invalid_input",
                "message": "append requires 'content'"}
    try:
        validate_drive_id(file_id, "file_id")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}
    if tab is not None:
        return _append_as_tab(file_id, content, tab, metadata)
    if metadata and metadata.get("mimeType") != GOOGLE_DOC_MIME:
        return plain_append(file_id, content, metadata)
    # Google Doc path only — see do_prepend for why this sits below the
    # plain-file routing (mise-melaso).
    content, cc_state = sanitise_for_insert(content)
    restore_cues = capture_restore_point(file_id)
    try:
        result = _append(file_id, content)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}
    apply_sanitise_cues(result.cues, cc_state)
    return merge_restore_cues(result, restore_cues)


# Always-on honesty cue for tab content: the rich-markdown door (Drive's
# import engine) cannot target a tab — aimed at one, it flattens the whole
# doc to a single tab (probed live 2026-08-24,
# docs/research/2026-08-24-givige-tab-probe/probe_drive_import_vs_tabs.py) —
# so tab placement is plain-text insertText and the cue says so every time.
_TAB_PLAIN_TEXT_NOTE = (
    "Tab content is plain text — markdown syntax is not rendered in tabs "
    "(Drive's markdown import cannot target a tab; probed 2026-08-24)."
)


def _append_as_tab(
    file_id: str,
    content: str,
    tab: str,
    metadata: dict[str, Any] | None,
) -> DoResult | dict[str, Any]:
    """Place content in a NEW tab — two sequential batchUpdates (doc_tabs).

    Existing tabs are read for the title-collision warning only; nothing
    here writes to them. Metadata=None (direct call, not via do()) falls
    through to the Google Doc path, matching this module's routing contract.
    """
    tab_title = tab.strip()
    if not tab_title:
        return {"error": True, "kind": "invalid_input",
                "message": "tab= requires a non-empty tab title"}
    mime = metadata.get("mimeType") if metadata else None
    if mime == GOOGLE_SHEET_MIME:
        return {
            "error": True, "kind": "invalid_input",
            "message": "tab= creates a Google DOC tab; this file is a "
                       "spreadsheet. Creating spreadsheet tabs isn't "
                       "supported yet — overwrite with range= writes to "
                       "existing sheet tabs only.",
        }
    if metadata and mime != GOOGLE_DOC_MIME:
        return {
            "error": True, "kind": "invalid_input",
            "message": f"tab= applies only to Google Docs — this file is "
                       f"{mime or 'of unknown type'}.",
        }
    try:
        doc_meta = get_doc_tabs_meta(file_id)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}
    existing_titles = [t["title"] for t in doc_meta["tabs"]]

    # Tab fill is the same insertText engine as prepend/append, so it eats
    # \f and \x00 the same way (mise-melaso).
    content, cc_state = sanitise_for_insert(content)

    restore_cues = capture_restore_point(file_id)
    try:
        minted = add_tab_with_content(file_id, tab_title, content)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    tab_id = minted.get("tabId")
    cues: dict[str, Any] = {
        "tab_id": tab_id,
        "tab_title": minted.get("title", tab_title),
        "tab_index": minted.get("index"),
        "inserted_chars": len(content),
        "note": _TAB_PLAIN_TEXT_NOTE,
    }
    apply_sanitise_cues(cues, cc_state)
    if tab_title in existing_titles:
        # append, not assign: a control-character disclosure may already be
        # sitting in this list, and a second warning must not silence it.
        cues.setdefault("warnings", []).append(
            f"A tab titled {tab_title!r} already existed — the new tab is a "
            f"second one with the same title (ids differ; the new one is "
            f"{tab_id})."
        )
    result = DoResult(
        file_id=file_id,
        title=doc_meta["title"],
        web_link=(
            f"https://docs.google.com/document/d/{file_id}/edit?tab={tab_id}"
        ),
        operation="append",
        cues=cues,
    )
    return merge_restore_cues(result, restore_cues)


def do_replace_text(
    file_id: str | None = None,
    find: str | None = None,
    content: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> DoResult | dict[str, Any]:
    """Find and replace all occurrences of text in a document or plain file."""
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "replace_text requires 'file_id'"}
    if not find:
        return {"error": True, "kind": "invalid_input",
                "message": "replace_text requires 'find'"}
    if content is None:
        return {"error": True, "kind": "invalid_input",
                "message": "replace_text requires 'content' (use empty string to delete matches)"}
    try:
        validate_drive_id(file_id, "file_id")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}
    if metadata and metadata.get("mimeType") == GOOGLE_SHEET_MIME:
        return sheet_replace_text(file_id, find, content, metadata)
    if metadata and metadata.get("mimeType") != GOOGLE_DOC_MIME:
        return plain_replace_text(file_id, find, content, metadata)
    # Google Doc path only (mise-melaso). The replacement text rides
    # replaceAllText, which eats \f and \x00 exactly as insertText does;
    # and a `find` carrying either can never match, because no Doc can hold
    # them — worth saying before the caller reads a 0-occurrence no-op as
    # "that text isn't in the document".
    find_warning = find_string_warning(find)
    content, cc_state = sanitise_for_insert(content)
    restore_cues = capture_restore_point(file_id)
    try:
        result = _replace_text(file_id, find, content)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}
    if isinstance(result, DoResult):
        apply_sanitise_cues(result.cues, cc_state)
        if find_warning:
            result.cues.setdefault("warnings", []).append(find_warning)
    # A no-op edit creates no revision, so the anchor captured above still points
    # at the LIVE document — accurate, but indistinguishable from the anchor of an
    # edit that landed, which is exactly the false reassurance nacolu is about.
    # Capture warnings stay: a failed read is still worth saying.
    if isinstance(result, DoResult) and result.cues.get("occurrences_changed") == 0:
        restore_cues.pop("restore_point", None)
    return merge_restore_cues(result, restore_cues)


@with_retry(max_attempts=3, delay_ms=1000)
def _prepend(file_id: str, text: str) -> DoResult:
    """Insert at index 1 (start of body)."""
    client = get_sync_client()
    meta = _get_doc_meta(client, file_id)

    client.post_json(
        f"{_DOCS_API}/{file_id}:batchUpdate",
        json_body={"requests": [{"insertText": {"location": _first_tab_location(meta, 1), "text": text}}]},
    )

    cues: dict[str, Any] = {"inserted_chars": len(text)}
    if warning := _multi_tab_insert_warning(meta, "prepend"):
        cues["warnings"] = [warning]
    return DoResult(
        file_id=file_id,
        title=meta["title"],
        web_link=f"https://docs.google.com/document/d/{file_id}/edit",
        operation="prepend",
        cues=cues,
    )


@with_retry(max_attempts=3, delay_ms=1000)
def _append(file_id: str, text: str) -> DoResult:
    """Insert at end of document body."""
    client = get_sync_client()
    meta = _get_doc_meta(client, file_id)

    # Insert before the final newline (endIndex - 1)
    insert_index = max(meta["end_index"] - 1, 1)

    client.post_json(
        f"{_DOCS_API}/{file_id}:batchUpdate",
        json_body={"requests": [{"insertText": {"location": _first_tab_location(meta, insert_index), "text": text}}]},
    )

    cues: dict[str, Any] = {"inserted_chars": len(text)}
    if warning := _multi_tab_insert_warning(meta, "append"):
        cues["warnings"] = [warning]
    return DoResult(
        file_id=file_id,
        title=meta["title"],
        web_link=f"https://docs.google.com/document/d/{file_id}/edit",
        operation="append",
        cues=cues,
    )


@with_retry(max_attempts=3, delay_ms=1000)
def _replace_text(file_id: str, find: str, replace: str) -> DoResult:
    """Replace all occurrences via replaceAllText."""
    client = get_sync_client()
    meta = _get_doc_meta(client, file_id)

    result = client.post_json(
        f"{_DOCS_API}/{file_id}:batchUpdate",
        json_body={"requests": [{
            "replaceAllText": {
                "containsText": {"text": find, "matchCase": True},
                "replaceText": replace,
            },
        }]},
    )

    # Extract replacement count from response
    replies = result.get("replies", [{}])
    occurrences = replies[0].get("replaceAllText", {}).get("occurrencesChanged", 0) if replies else 0

    cues: dict[str, Any] = {
        "find": find,
        "replace": replace,
        "occurrences_changed": occurrences,
    }
    if occurrences == 0:
        # Docs only: content.md is a *rendering*, so its markers aren't document
        # text. A plain .md file or a sheet cell holds them literally, which is
        # why the sibling paths take the bare warning.
        cues["warning"] = NO_MATCH_WARNING + markdown_marker_hint(find)
    # replaceAllText with no tabsCriteria applies to ALL tabs (discovery rev
    # 20260817; verified live on a 2-tab doc, essayeur 2026-08-24). On a
    # multi-tab doc that must be disclosed, not silent — a redraft tab
    # placed beside a live draft shares most of its strings with it.
    tab_titles = meta.get("tab_titles") or []
    if occurrences > 0 and len(tab_titles) > 1:
        names = ", ".join(repr(t) for t in tab_titles[:5])
        cues.setdefault("warnings", []).append(
            f"This doc has {len(tab_titles)} tabs ({names}) and replace_text "
            f"applies across ALL of them — occurrences_changed is the total "
            f"across tabs. To revert: cues.restore_point."
        )

    return DoResult(
        file_id=file_id,
        title=meta["title"],
        web_link=f"https://docs.google.com/document/d/{file_id}/edit",
        operation="replace_text",
        cues=cues,
    )
