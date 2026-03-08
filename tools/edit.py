"""
Surgical edit operations — prepend, append, replace_text on Google Docs and plain files.

Google Docs: Docs API batchUpdate with insertText and replaceAllText.
Plain files: Drive Files API (download → modify → re-upload).
Preserves existing content at other positions.

Routing contract: metadata is pre-fetched at dispatch level (server.py) and
passed via metadata= param. If metadata is None (direct call, not via do()),
we fall through to the Google Doc path for backward compatibility. This avoids
an extra Drive API call per edit — the dispatch fetches once, handlers share it.
"""

from typing import Any

from adapters.drive import GOOGLE_DOC_MIME
from adapters.services import get_docs_service
from models import DoResult, MiseError, ErrorKind
from retry import with_retry
from tools.plain_file import plain_prepend, plain_append, plain_replace_text
from validation import validate_drive_id


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
    try:
        return _prepend(file_id, content)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


def do_append(
    file_id: str | None = None,
    content: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> DoResult | dict[str, Any]:
    """Insert text at the end of a document or plain file."""
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
    if metadata and metadata.get("mimeType") != GOOGLE_DOC_MIME:
        return plain_append(file_id, content, metadata)
    try:
        return _append(file_id, content)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


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
    if metadata and metadata.get("mimeType") != GOOGLE_DOC_MIME:
        return plain_replace_text(file_id, find, content, metadata)
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
