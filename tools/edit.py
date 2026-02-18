"""
Surgical edit operations — prepend, append, replace_text on Google Docs.

Uses Docs API batchUpdate with insertText and replaceAllText.
Preserves existing content at other positions.
"""

from typing import Any

from adapters.services import get_docs_service
from models import DoResult, MiseError, ErrorKind
from retry import with_retry


def _get_doc_meta(service: Any, file_id: str) -> dict[str, Any]:
    """Fetch document title and end index."""
    doc = (
        service.documents()
        .get(documentId=file_id, fields="title,body(content(endIndex))")
        .execute()
    )
    body_content = doc.get("body", {}).get("content", [])
    end_index = body_content[-1].get("endIndex", 1) if body_content else 1
    return {"title": doc.get("title", "Untitled"), "end_index": end_index}


def do_prepend(file_id: str | None = None, content: str | None = None) -> DoResult | dict[str, Any]:
    """Insert text at the beginning of a Google Doc."""
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "prepend requires 'file_id'"}
    if not content:
        return {"error": True, "kind": "invalid_input",
                "message": "prepend requires 'content'"}
    try:
        return _prepend(file_id, content)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


def do_append(file_id: str | None = None, content: str | None = None) -> DoResult | dict[str, Any]:
    """Insert text at the end of a Google Doc."""
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "append requires 'file_id'"}
    if not content:
        return {"error": True, "kind": "invalid_input",
                "message": "append requires 'content'"}
    try:
        return _append(file_id, content)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


def do_replace_text(file_id: str | None = None, find: str | None = None, content: str | None = None) -> DoResult | dict[str, Any]:
    """Find and replace all occurrences of text in a Google Doc."""
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
        return _replace_text(file_id, find, content)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


@with_retry(max_attempts=3, delay_ms=1000)
def _prepend(file_id: str, text: str) -> DoResult:
    """Insert at index 1 (start of body)."""
    service = get_docs_service()
    meta = _get_doc_meta(service, file_id)

    service.documents().batchUpdate(
        documentId=file_id,
        body={"requests": [{"insertText": {"location": {"index": 1}, "text": text}}]},
    ).execute()

    return DoResult(
        file_id=file_id,
        title=meta["title"],
        web_link=f"https://docs.google.com/document/d/{file_id}/edit",
        operation="prepend",
        cues={"inserted_chars": len(text)},
    )


@with_retry(max_attempts=3, delay_ms=1000)
def _append(file_id: str, text: str) -> DoResult:
    """Insert at end of document body."""
    service = get_docs_service()
    meta = _get_doc_meta(service, file_id)

    # Insert before the final newline (endIndex - 1)
    insert_index = max(meta["end_index"] - 1, 1)

    service.documents().batchUpdate(
        documentId=file_id,
        body={"requests": [{"insertText": {"location": {"index": insert_index}, "text": text}}]},
    ).execute()

    return DoResult(
        file_id=file_id,
        title=meta["title"],
        web_link=f"https://docs.google.com/document/d/{file_id}/edit",
        operation="append",
        cues={"inserted_chars": len(text)},
    )


@with_retry(max_attempts=3, delay_ms=1000)
def _replace_text(file_id: str, find: str, replace: str) -> DoResult:
    """Replace all occurrences via replaceAllText."""
    service = get_docs_service()
    meta = _get_doc_meta(service, file_id)

    result = service.documents().batchUpdate(
        documentId=file_id,
        body={"requests": [{
            "replaceAllText": {
                "containsText": {"text": find, "matchCase": True},
                "replaceText": replace,
            },
        }]},
    ).execute()

    # Extract replacement count from response
    replies = result.get("replies", [{}])
    occurrences = replies[0].get("replaceAllText", {}).get("occurrencesChanged", 0) if replies else 0

    return DoResult(
        file_id=file_id,
        title=meta["title"],
        web_link=f"https://docs.google.com/document/d/{file_id}/edit",
        operation="replace_text",
        cues={
            "find": find,
            "replace": replace,
            "occurrences_changed": occurrences,
        },
    )
