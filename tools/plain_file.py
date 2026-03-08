"""
Plain file edit operations — overwrite, replace_text, prepend, append for non-Google-Doc files.

Uses Drive Files API: download content → modify in memory → re-upload.
Handles text/* and known text-safe MIME types (JSON, YAML, SVG, etc.).
Binary files are rejected for text operations.
"""

from pathlib import Path
from typing import Any

from adapters.drive import (
    download_file_content,
    upload_file_content,
    is_text_mime,
    is_google_workspace_file,
)
from models import DoResult
from retry import with_retry
from tools.common import resolve_source as _resolve_source


# Large file warning threshold (5MB)
LARGE_FILE_WARNING_BYTES = 5 * 1024 * 1024


def _reject_google_native(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Return error dict if file is a Google native type that shouldn't be here.

    Google Sheets, Slides, Forms etc. can't be downloaded via get_media() —
    they need their own APIs. If one reaches the plain file path, reject
    with a clear message rather than letting it fail with an opaque API error.
    Returns None if the file is fine to proceed.
    """
    mime = metadata.get("mimeType", "")
    if is_google_workspace_file(mime):
        kind = mime.split(".")[-1]  # "spreadsheet", "presentation", etc.
        return {
            "error": True,
            "kind": "invalid_input",
            "message": f"Edit operations on Google {kind.title()} files use a different API path. "
                       f"This operation is for plain files (markdown, JSON, etc.) stored in Drive.",
        }
    return None


def _decode_content(raw: bytes, file_name: str) -> str:
    """Decode file content as UTF-8, falling back to latin-1."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("latin-1")
        except UnicodeDecodeError:
            raise ValueError(
                f"File encoding not supported for text operations: {file_name}"
            )


def _make_result(
    file_id: str, metadata: dict[str, Any], operation: str, cues: dict[str, Any],
) -> DoResult:
    """Build a DoResult for a plain file operation."""
    cues["plain_file"] = True
    cues["mime_type"] = metadata.get("mimeType", "")
    return DoResult(
        file_id=file_id,
        title=metadata.get("name", "Untitled"),
        web_link=metadata.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view"),
        operation=operation,
        cues=cues,
    )


def _size_warning(metadata: dict[str, Any]) -> str | None:
    """Return warning string if file is large, None otherwise."""
    size = int(metadata.get("size", 0))
    if size > LARGE_FILE_WARNING_BYTES:
        mb = size / (1024 * 1024)
        return f"File is {mb:.1f}MB — text operations on large files may be slow"
    return None


# Mime types where prepend/append produce invalid files (XML/JSON structure breaks)
_STRUCTURED_MIMES = {
    "application/json", "application/xml", "application/xhtml+xml",
    "image/svg+xml", "text/xml", "text/html",
}


def _is_structured(mime_type: str) -> bool:
    """Check if MIME type is a structured format where prepend/append is risky."""
    return mime_type in _STRUCTURED_MIMES


def plain_overwrite(
    file_id: str,
    content: str | None,
    source: str | None,
    base_path: str | None,
    metadata: dict[str, Any],
) -> DoResult | dict[str, Any]:
    """Replace full content of a plain file via Drive Files API."""
    if err := _reject_google_native(metadata):
        return err
    # Resolve source
    try:
        resolved_source = _resolve_source(source, base_path)
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    if resolved_source and content:
        return {"error": True, "kind": "invalid_input",
                "message": "Provide 'content' or 'source', not both"}

    if resolved_source:
        # Read from deposit folder — look for any content file
        content_file = _find_content_file(resolved_source)
        if not content_file:
            return {"error": True, "kind": "invalid_input",
                    "message": f"No content file in source folder: {resolved_source}"}
        file_bytes = content_file.read_bytes()
    elif content:
        file_bytes = content.encode("utf-8")
    else:
        return {"error": True, "kind": "invalid_input",
                "message": "overwrite requires 'content' or 'source'"}

    mime_type = metadata.get("mimeType", "application/octet-stream")
    upload_file_content(file_id, file_bytes, mime_type)

    return _make_result(file_id, metadata, "overwrite", {
        "char_count": len(file_bytes),
    })


def plain_replace_text(
    file_id: str,
    find: str,
    replace: str,
    metadata: dict[str, Any],
) -> DoResult | dict[str, Any]:
    """Find and replace text in a plain file via download-modify-upload."""
    if err := _reject_google_native(metadata):
        return err
    mime_type = metadata.get("mimeType", "")
    if not is_text_mime(mime_type):
        return {"error": True, "kind": "invalid_input",
                "message": f"Text operations not supported on binary files ({mime_type}). Use overwrite for full replacement."}

    raw = download_file_content(file_id)
    text = _decode_content(raw, metadata.get("name", ""))

    count = text.count(find)
    if count == 0:
        return _make_result(file_id, metadata, "replace_text", {
            "find": find,
            "replace": replace,
            "occurrences_changed": 0,
            "warning": "Text not found",
        })

    new_text = text.replace(find, replace)
    upload_file_content(file_id, new_text.encode("utf-8"), mime_type)

    cues: dict[str, Any] = {
        "find": find,
        "replace": replace,
        "occurrences_changed": count,
    }
    warning = _size_warning(metadata)
    if warning:
        cues["warning"] = warning

    return _make_result(file_id, metadata, "replace_text", cues)


def plain_prepend(
    file_id: str,
    content: str,
    metadata: dict[str, Any],
) -> DoResult | dict[str, Any]:
    """Prepend text to a plain file via download-modify-upload."""
    if err := _reject_google_native(metadata):
        return err
    mime_type = metadata.get("mimeType", "")
    if not is_text_mime(mime_type):
        return {"error": True, "kind": "invalid_input",
                "message": f"Text operations not supported on binary files ({mime_type}). Use overwrite for full replacement."}

    raw = download_file_content(file_id)
    existing = _decode_content(raw, metadata.get("name", ""))

    new_text = content + existing
    upload_file_content(file_id, new_text.encode("utf-8"), mime_type)

    cues: dict[str, Any] = {"inserted_chars": len(content)}
    if _is_structured(mime_type):
        cues["structured_format"] = True
    warning = _size_warning(metadata)
    if warning:
        cues["warning"] = warning

    return _make_result(file_id, metadata, "prepend", cues)


def plain_append(
    file_id: str,
    content: str,
    metadata: dict[str, Any],
) -> DoResult | dict[str, Any]:
    """Append text to a plain file via download-modify-upload."""
    if err := _reject_google_native(metadata):
        return err
    mime_type = metadata.get("mimeType", "")
    if not is_text_mime(mime_type):
        return {"error": True, "kind": "invalid_input",
                "message": f"Text operations not supported on binary files ({mime_type}). Use overwrite for full replacement."}

    raw = download_file_content(file_id)
    existing = _decode_content(raw, metadata.get("name", ""))

    new_text = existing + content
    upload_file_content(file_id, new_text.encode("utf-8"), mime_type)

    cues: dict[str, Any] = {"inserted_chars": len(content)}
    if _is_structured(mime_type):
        cues["structured_format"] = True
    warning = _size_warning(metadata)
    if warning:
        cues["warning"] = warning

    return _make_result(file_id, metadata, "append", cues)


def _find_content_file(source_dir: Path) -> Path | None:
    """Find the content file in a deposit folder.

    Looks for content.md first (most common), then any content.* file.
    Returns None if no content file found.
    """
    content_md = source_dir / "content.md"
    if content_md.exists():
        return content_md

    # Look for any content.* file
    for f in sorted(source_dir.iterdir()):
        if f.name.startswith("content.") and f.is_file():
            return f

    return None
