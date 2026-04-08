"""
Overwrite operation — replace full content of a Google Doc or plain file.

Google Docs: Drive import (text/markdown → formatted Google Doc).
Plain files: Drive Files API (upload new content directly).

Preserves file ID, sharing, location, and revision history.

Routing contract: metadata is pre-fetched at dispatch level (server.py) and
passed via metadata= param. If metadata is None (direct call, not via do()),
we fall through to the Google Doc path for backward compatibility.
"""

from pathlib import Path
from typing import Any

from adapters.drive import GOOGLE_DOC_MIME, upload_file_content
from models import DoResult, MiseError
from tools.common import resolve_source as _resolve_source
from tools.plain_file import plain_overwrite
from validation import validate_drive_id


def do_overwrite(
    file_id: str | None = None,
    content: str | None = None,
    source: str | None = None,
    base_path: str | None = None,
    metadata: dict[str, Any] | None = None,
    file_path: str | None = None,
) -> DoResult | dict[str, Any]:
    """
    Replace full content of a Google Doc or plain file.

    Args:
        file_id: Target file ID
        content: Content string (mutually exclusive with source and file_path)
        source: Path to deposit folder with content file
        base_path: Working directory for resolving relative paths
        metadata: Pre-fetched file metadata (from dispatch). If None, assumes Google Doc.
        file_path: Local file path to read content from (no deposit folder needed)

    Returns:
        DoResult on success, error dict on failure
    """
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "overwrite requires 'file_id'"}
    try:
        validate_drive_id(file_id, "file_id")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    # Validate source path early (before API call)
    try:
        resolved_source = _resolve_source(source, base_path)
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    # Resolve file_path — read content directly from a local file
    if file_path:
        inputs = sum([content is not None, resolved_source is not None])
        if inputs > 0:
            return {
                "error": True,
                "kind": "invalid_input",
                "message": "Provide only one of 'content', 'source', or 'file_path'",
            }
        resolved = Path(file_path)
        if not resolved.is_absolute() and base_path:
            resolved = Path(base_path) / resolved
        resolved = resolved.resolve()
        if base_path:
            base_resolved = Path(base_path).resolve()
            if not str(resolved).startswith(str(base_resolved)):
                return {"error": True, "kind": "invalid_input",
                        "message": "file_path must be within the working directory"}
        if not resolved.exists():
            return {"error": True, "kind": "invalid_input",
                    "message": f"File not found: {file_path}"}
        if not resolved.is_file():
            return {"error": True, "kind": "invalid_input",
                    "message": f"Not a file: {file_path}"}
        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"error": True, "kind": "invalid_input",
                    "message": f"File is not valid UTF-8 text: {file_path}"}

    if resolved_source and content:
        return {
            "error": True,
            "kind": "invalid_input",
            "message": "Provide 'content' or 'source', not both",
        }

    if not content and not resolved_source:
        return {
            "error": True,
            "kind": "invalid_input",
            "message": "overwrite requires 'content', 'source', or 'file_path'",
        }

    # Route by file type: Google Docs → Drive import, plain files → Drive Files API
    if metadata and metadata.get("mimeType") != GOOGLE_DOC_MIME:
        return plain_overwrite(file_id, content, source, base_path, metadata)

    # Google Doc path — read content from source if needed
    if resolved_source:
        content_file = resolved_source / "content.md"
        if not content_file.exists():
            return {
                "error": True,
                "kind": "invalid_input",
                "message": f"No content.md in source folder: {resolved_source}",
            }
        content = content_file.read_text(encoding="utf-8")

    title = metadata.get("name", "Untitled") if metadata else None

    try:
        return _overwrite_doc(file_id, content, title=title)  # type: ignore[arg-type]
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


def _overwrite_doc(
    file_id: str, markdown: str, *, title: str | None = None,
) -> DoResult:
    """Replace document content via Drive import (markdown → formatted Google Doc).

    Uses files().update() with text/markdown media type, which triggers the same
    import conversion as files().create() — headings, bold, tables, lists all render.
    """
    result = upload_file_content(file_id, markdown.encode("utf-8"), "text/markdown")
    doc_title = title or result.get("name", "Untitled")

    return DoResult(
        file_id=file_id,
        title=doc_title,
        web_link=f"https://docs.google.com/document/d/{file_id}/edit",
        operation="overwrite",
        cues={"char_count": len(markdown)},
    )
