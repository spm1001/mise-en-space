"""
Create tool implementation.

Creates Google Workspace documents or plain files from content — either inline
or from a deposit folder (the deposit-then-publish pattern).

doc_type='file' uploads content as-is (no Google conversion). MIME type is
inferred from the title's file extension, or defaults to text/plain.

Uses httpx via MiseSyncClient (Phase 1 migration).
"""

import csv
import io
import json
import logging
import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adapters.http_client import get_sync_client
from adapters.drive import GOOGLE_DOC_MIME, GOOGLE_SHEET_MIME, GOOGLE_SLIDES_MIME
from adapters.sheets import add_sheet, update_sheet_values, rename_sheet
from models import DoResult, MiseError, ErrorKind
from retry import with_retry
from workspace import enrich_manifest
from tools.common import resolve_source as _resolve_source
from validation import validate_drive_id, sanitize_title

logger = logging.getLogger(__name__)

# Drive API v3 base URLs
_DRIVE_API = "https://www.googleapis.com/drive/v3/files"
_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"


def _mise_file_metadata(title: str, mime_type: str | None = None, folder_id: str | None = None) -> dict[str, Any]:
    """Build Drive file metadata with mise provenance stamped.

    Sets description (visible in Drive UI) and properties (searchable via
    Drive API: properties has { key='mise' and value='true' }).
    """
    metadata: dict[str, Any] = {
        "name": title,
        "description": "Created by mise-en-space MCP",
        "properties": {"mise": "true"},
    }
    if mime_type:
        metadata["mimeType"] = mime_type
    if folder_id:
        metadata["parents"] = [folder_id]
    return metadata


def _create_error(kind: str, message: str) -> dict[str, Any]:
    """Structured error dict for create operations."""
    return {"error": True, "kind": kind, "message": message}


def _resolve_folder_cues(
    result: dict[str, Any], folder_id: str | None = None
) -> dict[str, Any]:
    """
    Build folder cues from a Drive files().create() response.

    Resolves the parent folder name via an extra API call (non-critical —
    degrades to folder_id or 'My Drive' on failure).
    """
    parents = result.get("parents", [])
    folder_name = None
    if parents:
        try:
            client = get_sync_client()
            folder_meta = client.get_json(
                f"{_DRIVE_API}/{parents[0]}",
                params={"fields": "name", "supportsAllDrives": "true"},
            )
            folder_name = folder_meta.get("name")
        except Exception:
            pass  # Non-critical — cue degrades gracefully

    return {
        "folder": folder_name or ("My Drive" if not folder_id else folder_id),
        "folder_id": parents[0] if parents else folder_id,
    }


# Supported doc types and their target MIME types
DOC_TYPE_TO_MIME = {
    "doc": GOOGLE_DOC_MIME,
    "sheet": GOOGLE_SHEET_MIME,
    "slides": GOOGLE_SLIDES_MIME,
}

# Which content file to read from a deposit, per doc_type.
# For sheets: content.csv is the combined file (all tabs).
# Multi-tab deposits also have content_{tab_slug}.csv per tab —
# multi-tab creation (mise-jonofu) will read those individually.
_SOURCE_FILENAME = {
    "doc": "content.md",
    "sheet": "content.csv",
}


def _read_manifest(source_path: Path) -> dict[str, Any]:
    """Read manifest.json from a deposit folder. Returns {} if missing or invalid."""
    manifest_file = source_path / "manifest.json"
    if manifest_file.exists():
        try:
            return json.loads(manifest_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            pass
    return {}


def _read_source(source_path: Path, doc_type: str) -> tuple[str, str | None]:
    """
    Read content and optional title from a deposit folder.

    Args:
        source_path: Path to the deposit folder
        doc_type: 'doc' or 'sheet' — determines which file to read

    Returns:
        (content, title_from_manifest) — title is None if no manifest

    Raises:
        MiseError: If the expected content file is missing
    """
    filename = _SOURCE_FILENAME.get(doc_type)
    if not filename:
        raise MiseError(
            ErrorKind.INVALID_INPUT,
            f"source not supported for doc_type={doc_type}. Supported: {list(_SOURCE_FILENAME.keys())}",
        )

    content_file = source_path / filename
    if not content_file.exists():
        raise MiseError(
            ErrorKind.INVALID_INPUT,
            f"Expected {filename} in {source_path}, but file not found.",
        )

    content = content_file.read_text(encoding="utf-8")
    manifest = _read_manifest(source_path)

    return content, manifest.get("title")


def _read_multi_tab_source(source_path: Path) -> list[tuple[str, str]]:
    """
    Read per-tab CSV files from a multi-tab deposit.

    Uses manifest.json tabs array for ordering and naming.

    Returns:
        List of (tab_name, csv_content) in manifest order.

    Raises:
        MiseError: If manifest has no tabs or files are missing.
    """
    manifest = _read_manifest(source_path)
    tabs = manifest.get("tabs", [])
    if not tabs:
        raise MiseError(
            ErrorKind.INVALID_INPUT,
            f"No tabs array in manifest at {source_path}. Not a multi-tab deposit.",
        )

    result: list[tuple[str, str]] = []
    for tab in tabs:
        tab_file = source_path / tab["filename"]
        if not tab_file.exists():
            raise MiseError(
                ErrorKind.INVALID_INPUT,
                f"Tab file {tab['filename']} listed in manifest but not found in {source_path}.",
            )
        result.append((tab["name"], tab_file.read_text(encoding="utf-8")))

    return result


def _csv_text_to_values(csv_text: str) -> list[list[str]]:
    """Parse CSV text into a 2D list of strings for Sheets API."""
    reader = csv.reader(io.StringIO(csv_text))
    return [row for row in reader]


def do_create(
    content: str | None = None,
    title: str | None = None,
    doc_type: str = "doc",
    folder_id: str | None = None,
    source: str | None = None,
    base_path: str | None = None,
    file_path: str | None = None,
    page_setup: str | None = None,
) -> DoResult | dict[str, Any]:
    """
    Create a Google Workspace document.

    Content comes from either:
    - `content` param (inline, backwards compatible)
    - `source` param (path to deposit folder with content.md or content.csv)
    - `file_path` param (local file — binary for doc_type='file', text for doc/sheet)

    Args:
        content: Inline content (markdown for doc, CSV for sheet)
        title: Document title (falls back to manifest title when using source)
        doc_type: 'doc' | 'sheet' | 'slides' | 'file'
        folder_id: Optional destination folder ID
        source: Path to deposit folder to read content from
        base_path: Working directory for resolving relative source paths
        file_path: Local file path to read content from
        page_setup: Page layout for doc creation. 'pageless' creates a pageless doc.

    Returns:
        DoResult on success, error dict on failure
    """
    # Validate page_setup
    if page_setup and page_setup != "pageless":
        return _create_error("invalid_input", f"Unsupported page_setup: {page_setup}. Supported: 'pageless'")
    if page_setup and doc_type != "doc":
        return _create_error("invalid_input", "page_setup is only valid for doc_type='doc'")
    # Resolve source path
    try:
        resolved_source = _resolve_source(source, base_path)
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    if folder_id:
        try:
            validate_drive_id(folder_id, "folder_id")
        except ValueError as e:
            return {"error": True, "kind": "invalid_input", "message": str(e)}

    if title:
        title = sanitize_title(title)

    # Resolve and validate file_path
    resolved_file_path: Path | None = None
    if file_path:
        if content:
            return _create_error("invalid_input", "Provide either 'content' or 'file_path', not both.")
        if source:
            return _create_error("invalid_input", "Provide either 'source' or 'file_path', not both.")

        resolved_file_path = Path(file_path)
        if not resolved_file_path.is_absolute() and base_path:
            resolved_file_path = Path(base_path) / resolved_file_path
        resolved_file_path = resolved_file_path.resolve()

        # Containment check — file_path must be under base_path
        if base_path:
            base_resolved = Path(base_path).resolve()
            if not str(resolved_file_path).startswith(str(base_resolved)):
                return _create_error("invalid_input", "file_path must be within the working directory.")

        if not resolved_file_path.exists():
            return _create_error("invalid_input", f"File not found: {file_path}")
        if not resolved_file_path.is_file():
            return _create_error("invalid_input", f"Not a file: {file_path}")

        # Infer title from filename if not provided
        if not title:
            title = resolved_file_path.name

        # For doc/sheet: read file as text content (no binary path needed)
        if doc_type in ("doc", "sheet"):
            try:
                content = resolved_file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return _create_error("invalid_input", f"File is not valid UTF-8 text: {file_path}")
            resolved_file_path = None  # Content extracted — don't pass as binary

    if not content and not source and not file_path:
        return {"error": True, "kind": "invalid_input",
                "message": "create requires 'content', 'source', or 'file_path'"}

    return _do_create_internal(content, title, doc_type, folder_id, resolved_source, resolved_file_path, base_path, page_setup=page_setup)


def _do_create_internal(
    content: str | None = None,
    title: str | None = None,
    doc_type: str = "doc",
    folder_id: str | None = None,
    source: Path | None = None,
    file_path: Path | None = None,
    base_path: str | None = None,
    page_setup: str | None = None,
) -> DoResult | dict[str, Any]:
    """Internal create logic. Returns DoResult on success, error dict on failure."""
    # Validate doc_type
    valid_types = list(DOC_TYPE_TO_MIME.keys()) + ["file"]
    if doc_type not in valid_types:
        return _create_error(
            "invalid_input",
            f"Unsupported doc_type: {doc_type}. Must be one of: {valid_types}",
        )

    # Resolve content from source or inline
    if source and content:
        return _create_error("invalid_input", "Provide either 'content' or 'source', not both.")

    # Check for multi-tab sheet deposit (source with tabs in manifest)
    multi_tab_data: list[tuple[str, str]] | None = None
    if source and doc_type == "sheet":
        manifest = _read_manifest(source)
        if manifest.get("tabs"):
            try:
                multi_tab_data = _read_multi_tab_source(source)
            except MiseError as e:
                return _create_error(e.kind.value, e.message)
            if not title:
                title = manifest.get("title")

    if source and not multi_tab_data:
        if doc_type == "file":
            return _create_error(
                "invalid_input",
                "doc_type='file' does not support source. Pass content inline.",
            )
        try:
            source_content, manifest_title = _read_source(source, doc_type)
        except MiseError as e:
            return _create_error(e.kind.value, e.message)
        content = source_content
        if not title:
            title = manifest_title

    if not multi_tab_data and not content and not file_path:
        return _create_error("invalid_input", "No content provided. Pass 'content', 'source', or 'file_path'.")
    if not title:
        return _create_error(
            "invalid_input",
            "No title provided. Pass 'title' or include it in the source manifest.",
        )

    # Check for local image refs in doc content
    image_refs: list[_ImageRef] = []
    image_base_path: Path | None = None
    if doc_type == "doc" and content and _IMAGE_REF_RE.search(content):
        content, image_refs = _parse_image_refs(content)
        # Resolve image paths relative to source folder or base_path
        image_base_path = source.parent if source else (Path(base_path) if base_path else None)  # type: ignore[arg-type]

    try:
        if doc_type == "file":
            file_bytes = file_path.read_bytes() if file_path else None
            result = _create_file(content, title, folder_id, file_bytes=file_bytes)
        elif doc_type == "doc":
            result = _create_doc(content, title, folder_id)
        elif doc_type == "sheet" and multi_tab_data:
            result = _create_multi_tab_sheet(multi_tab_data, title, folder_id)
        elif doc_type == "sheet":
            result = _create_sheet(content, title, folder_id)
        else:
            return _create_error(
                "not_implemented",
                f"Creating {doc_type} is not yet implemented. Currently supported: doc, sheet, file.",
            )

        # Post-creation: set pageless mode if requested
        if page_setup == "pageless" and isinstance(result, DoResult):
            try:
                _set_pageless(result.file_id)
                result.cues["page_setup"] = "pageless"
            except Exception as e:
                result.cues["page_setup_error"] = str(e)

        # Post-creation: embed local images if any were found
        if image_refs and isinstance(result, DoResult):
            embed_result = _embed_images_in_doc(result.file_id, image_refs, image_base_path)
            if embed_result.get("images_embedded"):
                result.cues["images_embedded"] = embed_result["images_embedded"]
            if embed_result.get("image_errors"):
                result.cues["image_errors"] = embed_result["image_errors"]

        # Enrich manifest if created from source
        if source and isinstance(result, DoResult):
            try:
                enrich_manifest(source, {
                    "status": "created",
                    "file_id": result.file_id,
                    "web_link": result.web_link,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            except FileNotFoundError:
                pass  # No manifest to enrich — deposit was content-only

        return result
    except MiseError as e:
        return _create_error(e.kind.value, e.message)
    except Exception as e:
        return _create_error("unknown", f"Unexpected error creating {doc_type}: {e}")


# Extensions that Python's mimetypes module doesn't know about
_EXTRA_MIME_TYPES: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".toml": "application/toml",
}


def _infer_mime_type(title: str) -> str:
    """Infer MIME type from file extension in title. Defaults to text/plain."""
    ext = Path(title).suffix.lower()
    if ext in _EXTRA_MIME_TYPES:
        return _EXTRA_MIME_TYPES[ext]
    mime, _ = mimetypes.guess_type(title)
    return mime or "text/plain"


@with_retry(max_attempts=3, delay_ms=1000)
def _create_file(
    content: str | None,
    title: str,
    folder_id: str | None = None,
    file_bytes: bytes | None = None,
) -> DoResult:
    """
    Upload a plain file to Drive without Google conversion.

    MIME type is inferred from the title's file extension (e.g. .md → text/markdown,
    .svg → image/svg+xml, .json → application/json). Falls back to text/plain.

    Content comes from either:
    - file_bytes (binary upload from file_path — PNG, DOCX, PDF etc.)
    - content (text, encoded to UTF-8)

    The file stays as-is in Drive — no conversion to Google Doc/Sheet/Slides.
    """
    client = get_sync_client()
    mime_type = _infer_mime_type(title)

    file_metadata = _mise_file_metadata(title, folder_id=folder_id)

    upload_bytes = file_bytes if file_bytes is not None else content.encode("utf-8")  # type: ignore[union-attr]

    logger.info("create file: title=%r mime=%s folder=%s binary=%s", title, mime_type, folder_id, file_bytes is not None)

    result = client.upload_multipart(
        _UPLOAD_API, file_metadata, upload_bytes, mime_type,
        params={"uploadType": "multipart", "fields": "id,webViewLink,name,parents", "supportsAllDrives": "true"},
    )

    cues = _resolve_folder_cues(result, folder_id)
    cues["plain_file"] = True
    cues["mime_type"] = mime_type

    return DoResult(
        file_id=result["id"],
        web_link=result.get("webViewLink", f"https://drive.google.com/file/d/{result['id']}/view"),
        title=result.get("name", title),
        operation="create",
        cues=cues,
        extras={"type": "file"},
    )


@with_retry(max_attempts=3, delay_ms=1000)
def _create_doc(
    content: str,
    title: str,
    folder_id: str | None = None,
) -> DoResult:
    """
    Create a Google Doc from markdown using Drive's native import.

    Drive automatically converts text/markdown to Google Doc format.
    This was discovered via about.get(fields='importFormats') - not in static docs!
    """
    client = get_sync_client()

    file_metadata = _mise_file_metadata(title, mime_type=GOOGLE_DOC_MIME, folder_id=folder_id)

    logger.info("create doc: title=%r folder=%s content_len=%d", title, folder_id, len(content))

    result = client.upload_multipart(
        _UPLOAD_API, file_metadata, content.encode("utf-8"), "text/markdown",
        params={"uploadType": "multipart", "fields": "id,webViewLink,name,parents", "supportsAllDrives": "true"},
    )

    cues = _resolve_folder_cues(result, folder_id)

    return DoResult(
        file_id=result["id"],
        web_link=result["webViewLink"],
        title=result.get("name", title),
        operation="create",
        cues=cues,
        extras={"type": "doc"},
    )


@with_retry(max_attempts=3, delay_ms=1000)
def _create_sheet(
    content: str,
    title: str,
    folder_id: str | None = None,
) -> DoResult:
    """
    Create a Google Sheet from CSV using Drive's native import.

    Drive converts text/csv to Google Sheet automatically.
    Same pattern as _create_doc with text/markdown.
    """
    client = get_sync_client()

    file_metadata = _mise_file_metadata(title, mime_type=GOOGLE_SHEET_MIME, folder_id=folder_id)

    logger.info("create sheet: title=%r folder=%s content_len=%d", title, folder_id, len(content))

    result = client.upload_multipart(
        _UPLOAD_API, file_metadata, content.encode("utf-8"), "text/csv",
        params={"uploadType": "multipart", "fields": "id,webViewLink,name,parents", "supportsAllDrives": "true"},
    )

    cues = _resolve_folder_cues(result, folder_id)

    return DoResult(
        file_id=result["id"],
        web_link=result["webViewLink"],
        title=result.get("name", title),
        operation="create",
        cues=cues,
        extras={"type": "sheet"},
    )


def _create_multi_tab_sheet(
    tabs: list[tuple[str, str]],
    title: str,
    folder_id: str | None = None,
) -> DoResult | dict[str, Any]:
    """
    Create a multi-tab Google Sheet using hybrid path.

    Strategy:
    1. CSV upload for tab 1 (fast, 94% type detection by Drive)
    2. Rename tab 1 from CSV filename to actual tab name
    3. For each additional tab: addSheet + values().update(USER_ENTERED)

    USER_ENTERED preserves formulae (cells starting with =) and auto-detects
    dates, numbers, booleans — same behaviour as typing into a cell.
    """
    if not tabs:
        return _create_error("invalid_input", "No tabs provided for multi-tab sheet.")

    first_tab_name, first_tab_csv = tabs[0]

    # Step 1: CSV upload creates the spreadsheet with tab 1
    result = _create_sheet(first_tab_csv, title, folder_id)
    if isinstance(result, dict):
        return result

    spreadsheet_id = result.file_id

    # Step 2: Rename tab 1 to actual tab name (CSV upload names it after filename)
    try:
        rename_sheet(spreadsheet_id, sheet_id=0, new_title=first_tab_name)
    except Exception:
        pass  # Non-critical — tab will just have a generic name

    # Step 3: Add remaining tabs via Sheets API
    tab_count = 1
    for tab_name, tab_csv in tabs[1:]:
        add_sheet(spreadsheet_id, tab_name)
        values = _csv_text_to_values(tab_csv)
        if values:
            update_sheet_values(
                spreadsheet_id,
                range_=f"'{tab_name}'!A1",
                values=values,
            )
        tab_count += 1

    # Update cues with tab info
    result.cues["tab_count"] = tab_count
    result.cues["tab_names"] = [name for name, _ in tabs]

    return result


# ============================================================================
# PAGE SETUP — post-creation document style changes
# ============================================================================

_DOCS_API = "https://docs.googleapis.com/v1/documents"


def _set_pageless(doc_id: str) -> None:
    """Set a Google Doc to pageless mode via Docs API batchUpdate.

    Uses UpdateDocumentStyleRequest with documentFormat.documentMode = PAGELESS.
    """
    client = get_sync_client()
    client.post_json(
        f"{_DOCS_API}/{doc_id}:batchUpdate",
        json_body={
            "requests": [
                {
                    "updateDocumentStyle": {
                        "documentStyle": {
                            "documentFormat": {
                                "documentMode": "PAGELESS",
                            },
                        },
                        "fields": "documentFormat",
                    }
                }
            ]
        },
    )


# ============================================================================
# IMAGE EMBEDDING — post-creation injection of local images into Google Docs
# ============================================================================

# Regex for markdown image refs: ![alt](path)
# Skips http/https URLs — those are external, not local files.
_IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\((?!https?://)([^)]+)\)")

# Sentinel prefix unlikely to appear in real content
_PLACEHOLDER_PREFIX = "\u3014MISE_IMG_"
_PLACEHOLDER_SUFFIX = "\u3015"


@dataclass
class _ImageRef:
    """A local image reference parsed from markdown."""
    index: int
    alt: str
    path: str
    placeholder: str


def _parse_image_refs(content: str) -> tuple[str, list[_ImageRef]]:
    """Parse markdown for local image refs, replace with unique placeholders.

    Returns (modified_content, list_of_refs). Remote URLs are left as-is.
    """
    refs: list[_ImageRef] = []

    def _replace(match: re.Match) -> str:
        alt = match.group(1)
        path = match.group(2)
        idx = len(refs)
        placeholder = f"{_PLACEHOLDER_PREFIX}{idx}{_PLACEHOLDER_SUFFIX}"
        refs.append(_ImageRef(index=idx, alt=alt, path=path, placeholder=placeholder))
        return placeholder

    modified = _IMAGE_REF_RE.sub(_replace, content)
    return modified, refs


def _resolve_image_path(ref_path: str, base_path: Path | None) -> Path | None:
    """Resolve an image path relative to base_path. Returns None if not found."""
    p = Path(ref_path)
    if p.is_absolute():
        return p if p.exists() else None
    if base_path:
        resolved = (base_path / p).resolve()
        return resolved if resolved.exists() else None
    return None


def _upload_temp_image(image_bytes: bytes, filename: str, mime_type: str) -> str:
    """Upload image bytes to Drive as a temp file. Returns file ID."""
    client = get_sync_client()
    metadata = {
        "name": f"_mise_temp_{filename}",
        "description": "Temporary image for mise doc embedding — safe to delete",
    }
    result = client.upload_multipart(
        _UPLOAD_API, metadata, image_bytes, mime_type,
        params={"uploadType": "multipart", "fields": "id", "supportsAllDrives": "true"},
    )
    return result["id"]


def _share_publicly(file_id: str) -> str:
    """Make a Drive file publicly readable (required for Docs API insertInlineImage).

    Returns the permission ID for later revocation.
    """
    client = get_sync_client()
    result = client.post_json(
        f"{_DRIVE_API}/{file_id}/permissions",
        json_body={"role": "reader", "type": "anyone"},
        params={"supportsAllDrives": "true", "fields": "id"},
    )
    return result.get("id", "anyoneWithLink")


def _revoke_public(file_id: str, permission_id: str) -> None:
    """Revoke a specific permission from a Drive file."""
    client = get_sync_client()
    try:
        client.request(
            "DELETE",
            f"{_DRIVE_API}/{file_id}/permissions/{permission_id}",
            params={"supportsAllDrives": "true"},
        )
    except Exception:
        pass  # Best-effort cleanup


def _delete_temp_file(file_id: str) -> None:
    """Delete a temporary Drive file."""
    client = get_sync_client()
    try:
        client.request(
            "DELETE",
            f"{_DRIVE_API}/{file_id}",
            params={"supportsAllDrives": "true"},
        )
    except Exception:
        pass  # Best-effort cleanup


def _find_placeholder_indices(
    doc_id: str, placeholders: list[str],
) -> dict[str, tuple[int, int]]:
    """Find start/end indices of placeholder text in a Google Doc.

    Returns {placeholder: (startIndex, endIndex)} for each found placeholder.

    Concatenates all text runs in each paragraph before searching, so
    placeholders that span text run boundaries are still found.
    """
    client = get_sync_client()
    doc = client.get_json(
        f"https://docs.googleapis.com/v1/documents/{doc_id}",
        params={"fields": "body(content(paragraph(elements(textRun(content),startIndex,endIndex))))"},
    )

    result: dict[str, tuple[int, int]] = {}
    placeholder_set = set(placeholders)

    for item in doc.get("body", {}).get("content", []):
        elements = item.get("paragraph", {}).get("elements", [])
        if not elements:
            continue

        # Concatenate all text runs in this paragraph with their absolute positions
        para_text = ""
        para_start = elements[0].get("startIndex", 0)
        for elem in elements:
            para_text += elem.get("textRun", {}).get("content", "")

        # Search for each placeholder in the concatenated paragraph text
        for ph in placeholder_set:
            offset = para_text.find(ph)
            if offset >= 0:
                start = para_start + offset
                end = start + len(ph)
                result[ph] = (start, end)

    return result


def _embed_images_in_doc(
    doc_id: str,
    refs: list[_ImageRef],
    base_path: Path | None,
) -> dict[str, Any]:
    """Post-creation: embed local images into a Google Doc.

    Uploads images to Drive, temporarily shares publicly, inserts via
    Docs batchUpdate, then revokes permissions and cleans up.

    Returns dict with images_embedded count and image_errors list.
    """
    from extractors.image import resize_image_bytes
    from adapters.image import is_svg, render_svg_to_png

    if not refs:
        return {}

    # Phase 1: Prepare images — resolve paths, read bytes, resize
    prepared: list[tuple[_ImageRef, bytes, str]] = []  # (ref, bytes, mime_type)
    errors: list[str] = []
    for ref in refs:
        resolved = _resolve_image_path(ref.path, base_path)
        if not resolved:
            errors.append(f"Image not found: {ref.path}")
            continue

        try:
            image_bytes = resolved.read_bytes()
            mime_type = mimetypes.guess_type(str(resolved))[0] or "image/png"

            # SVG → PNG conversion (Docs API can't display SVG inline)
            if is_svg(mime_type):
                png_bytes, _, _ = render_svg_to_png(image_bytes)
                if png_bytes:
                    image_bytes = png_bytes
                    mime_type = "image/png"
                else:
                    errors.append(f"SVG rendering failed: {ref.path}")
                    continue

            # Resize if needed
            resized = resize_image_bytes(image_bytes, mime_type)
            image_bytes = resized.content_bytes
            mime_type = resized.mime_type

            prepared.append((ref, image_bytes, mime_type))
        except Exception as e:
            errors.append(f"Image processing failed for {ref.path}: {e}")

    if not prepared:
        return {"image_errors": errors} if errors else {}

    # Phase 2: Upload images to Drive and share publicly
    uploaded: list[tuple[_ImageRef, str, str]] = []  # (ref, drive_file_id, permission_id)
    for ref, image_bytes, mime_type in prepared:
        try:
            filename = Path(ref.path).name
            file_id = _upload_temp_image(image_bytes, filename, mime_type)
            perm_id = _share_publicly(file_id)
            uploaded.append((ref, file_id, perm_id))
        except Exception as e:
            errors.append(f"Upload failed for {ref.path}: {e}")

    if not uploaded:
        return {"image_errors": errors} if errors else {}

    # Phase 3: Find placeholder indices in the created doc
    placeholders = [ref.placeholder for ref, _, _ in uploaded]
    indices = _find_placeholder_indices(doc_id, placeholders)

    # Phase 4: Build batchUpdate requests in reverse index order
    requests: list[dict[str, Any]] = []
    for ref, file_id, _ in uploaded:
        if ref.placeholder not in indices:
            errors.append(f"Placeholder not found in doc for {ref.path}")
            continue
        start, end = indices[ref.placeholder]
        requests.append((start, end, file_id))

    # Sort by start index descending — prevents index drift
    requests.sort(key=lambda x: x[0], reverse=True)

    batch_requests: list[dict[str, Any]] = []
    for start, end, file_id in requests:
        # Delete the placeholder text
        batch_requests.append({
            "deleteContentRange": {
                "range": {"startIndex": start, "endIndex": end, "segmentId": ""},
            }
        })
        # Insert image at the same position
        batch_requests.append({
            "insertInlineImage": {
                "uri": f"https://drive.google.com/uc?export=view&id={file_id}",
                "location": {"index": start, "segmentId": ""},
            }
        })

    if batch_requests:
        try:
            client = get_sync_client()
            client.post_json(
                f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
                json_body={"requests": batch_requests},
            )
        except Exception as e:
            errors.append(f"Docs batchUpdate failed: {e}")

    # Phase 5: Revoke permissions and delete temp files
    for _, file_id, perm_id in uploaded:
        _revoke_public(file_id, perm_id)
        _delete_temp_file(file_id)

    result: dict[str, Any] = {"images_embedded": len(requests)}
    if errors:
        result["image_errors"] = errors
    return result
