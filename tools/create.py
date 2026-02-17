"""
Create tool implementation.

Creates Google Workspace documents from content — either inline or from a
deposit folder (the deposit-then-publish pattern).
"""

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from googleapiclient.http import MediaIoBaseUpload

from adapters.services import get_drive_service
from adapters.drive import GOOGLE_DOC_MIME, GOOGLE_SHEET_MIME, GOOGLE_SLIDES_MIME
from models import CreateResult, CreateError, MiseError, ErrorKind
from retry import with_retry
from workspace import enrich_manifest


# Supported doc types and their target MIME types
DOC_TYPE_TO_MIME = {
    "doc": GOOGLE_DOC_MIME,
    "sheet": GOOGLE_SHEET_MIME,
    "slides": GOOGLE_SLIDES_MIME,
}

# Which content file to read from a deposit, per doc_type
_SOURCE_FILENAME = {
    "doc": "content.md",
    "sheet": "content.csv",
}


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

    # Try to read title from manifest
    title = None
    manifest_file = source_path / "manifest.json"
    if manifest_file.exists():
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            title = manifest.get("title")
        except (json.JSONDecodeError, KeyError):
            pass  # Manifest is optional metadata, not critical

    return content, title


def do_create(
    content: str | None = None,
    title: str | None = None,
    doc_type: str = "doc",
    folder_id: str | None = None,
    source: Path | None = None,
) -> CreateResult | CreateError:
    """
    Create a Google Workspace document.

    Content comes from either:
    - `content` param (inline, backwards compatible)
    - `source` param (path to deposit folder with content.md or content.csv)

    Args:
        content: Inline content (markdown for doc, CSV for sheet)
        title: Document title (falls back to manifest title when using source)
        doc_type: 'doc' | 'sheet' | 'slides'
        folder_id: Optional destination folder ID
        source: Path to deposit folder to read content from

    Returns:
        CreateResult with file_id and web_link, or CreateError on failure
    """
    # Validate doc_type
    if doc_type not in DOC_TYPE_TO_MIME:
        return CreateError(
            kind="invalid_input",
            message=f"Unsupported doc_type: {doc_type}. Must be one of: {list(DOC_TYPE_TO_MIME.keys())}",
        )

    # Resolve content from source or inline
    if source and content:
        return CreateError(
            kind="invalid_input",
            message="Provide either 'content' or 'source', not both.",
        )

    if source:
        try:
            source_content, manifest_title = _read_source(source, doc_type)
        except MiseError as e:
            return CreateError(kind=e.kind.value, message=e.message)
        content = source_content
        if not title:
            title = manifest_title

    if not content:
        return CreateError(
            kind="invalid_input",
            message="No content provided. Pass 'content' or 'source'.",
        )
    if not title:
        return CreateError(
            kind="invalid_input",
            message="No title provided. Pass 'title' or include it in the source manifest.",
        )

    try:
        if doc_type == "doc":
            result = _create_doc(content, title, folder_id)
        elif doc_type == "sheet":
            result = _create_sheet(content, title, folder_id)
        else:
            return CreateError(
                kind="not_implemented",
                message=f"Creating {doc_type} is not yet implemented. Currently supported: doc, sheet.",
            )

        # Enrich manifest if created from source
        if source and isinstance(result, CreateResult):
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
        return CreateError(kind=e.kind.value, message=e.message)
    except Exception as e:
        return CreateError(kind="unknown", message=f"Unexpected error creating {doc_type}: {e}")


@with_retry(max_attempts=3, delay_ms=1000)
def _create_doc(
    content: str,
    title: str,
    folder_id: str | None = None,
) -> CreateResult | CreateError:
    """
    Create a Google Doc from markdown using Drive's native import.

    Drive automatically converts text/markdown to Google Doc format.
    This was discovered via about.get(fields='importFormats') - not in static docs!
    """
    service = get_drive_service()

    # File metadata
    file_metadata: dict[str, Any] = {
        "name": title,
        "mimeType": GOOGLE_DOC_MIME,
    }

    # Add parent folder if specified
    if folder_id:
        file_metadata["parents"] = [folder_id]

    # Create media with markdown content
    # Drive's import converts text/markdown -> Google Doc
    media = MediaIoBaseUpload(
        io.BytesIO(content.encode("utf-8")),
        mimetype="text/markdown",
        resumable=True,
    )

    # Create the file
    result = (
        service.files()
        .create(
            body=file_metadata,
            media_body=media,
            fields="id,webViewLink,name,parents",
            supportsAllDrives=True,
        )
        .execute()
    )

    # Build cues: resolve parent folder name
    parents = result.get("parents", [])
    folder_name = None
    if parents:
        try:
            folder_meta = (
                service.files()
                .get(fileId=parents[0], fields="name", supportsAllDrives=True)
                .execute()
            )
            folder_name = folder_meta.get("name")
        except Exception:
            pass  # Non-critical — cue degrades gracefully

    cues: dict[str, Any] = {
        "folder": folder_name or ("My Drive" if not folder_id else folder_id),
        "folder_id": parents[0] if parents else folder_id,
    }

    return CreateResult(
        file_id=result["id"],
        web_link=result["webViewLink"],
        title=result.get("name", title),
        doc_type="doc",
        cues=cues,
    )


@with_retry(max_attempts=3, delay_ms=1000)
def _create_sheet(
    content: str,
    title: str,
    folder_id: str | None = None,
) -> CreateResult | CreateError:
    """
    Create a Google Sheet from CSV using Drive's native import.

    Drive converts text/csv to Google Sheet automatically.
    Same pattern as _create_doc with text/markdown.
    """
    service = get_drive_service()

    file_metadata: dict[str, Any] = {
        "name": title,
        "mimeType": GOOGLE_SHEET_MIME,
    }

    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaIoBaseUpload(
        io.BytesIO(content.encode("utf-8")),
        mimetype="text/csv",
        resumable=True,
    )

    result = (
        service.files()
        .create(
            body=file_metadata,
            media_body=media,
            fields="id,webViewLink,name,parents",
            supportsAllDrives=True,
        )
        .execute()
    )

    # Build cues: resolve parent folder name
    parents = result.get("parents", [])
    folder_name = None
    if parents:
        try:
            folder_meta = (
                service.files()
                .get(fileId=parents[0], fields="name", supportsAllDrives=True)
                .execute()
            )
            folder_name = folder_meta.get("name")
        except Exception:
            pass

    cues: dict[str, Any] = {
        "folder": folder_name or ("My Drive" if not folder_id else folder_id),
        "folder_id": parents[0] if parents else folder_id,
    }

    return CreateResult(
        file_id=result["id"],
        web_link=result["webViewLink"],
        title=result.get("name", title),
        doc_type="sheet",
        cues=cues,
    )
