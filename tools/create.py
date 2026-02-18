"""
Create tool implementation.

Creates Google Workspace documents from content — either inline or from a
deposit folder (the deposit-then-publish pattern).
"""

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from googleapiclient.http import MediaIoBaseUpload

from adapters.services import get_drive_service
from adapters.drive import GOOGLE_DOC_MIME, GOOGLE_SHEET_MIME, GOOGLE_SLIDES_MIME
from adapters.sheets import add_sheet, update_sheet_values, rename_sheet
from models import CreateResult, CreateError, DoResult, MiseError, ErrorKind
from retry import with_retry
from workspace import enrich_manifest


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


def _resolve_source(source: str | None, base_path: str | None) -> Path | None:
    """Resolve source path relative to base_path.

    Returns None if no source. Raises ValueError if source given without base_path.
    """
    if not source:
        return None
    if not base_path:
        raise ValueError("base_path is required when using source — pass your working directory")
    source_path = Path(source)
    return source_path if source_path.is_absolute() else Path(base_path) / source_path


def do_create(
    content: str | None = None,
    title: str | None = None,
    doc_type: str = "doc",
    folder_id: str | None = None,
    source: str | None = None,
    base_path: str | None = None,
) -> DoResult | dict[str, Any]:
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
        base_path: Working directory for resolving relative source paths

    Returns:
        DoResult on success, error dict on failure
    """
    # Resolve source path
    try:
        resolved_source = _resolve_source(source, base_path)
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    if not content and not source:
        return {"error": True, "kind": "invalid_input",
                "message": "create requires 'content' or 'source'"}

    result = _do_create_internal(content, title, doc_type, folder_id, resolved_source)

    # Wrap CreateResult → DoResult at boundary
    if isinstance(result, CreateResult):
        return DoResult(
            file_id=result.file_id,
            title=result.title,
            web_link=result.web_link,
            operation="create",
            cues=result.cues or {},
            extras={"type": result.doc_type},
        )
    elif isinstance(result, CreateError):
        return result.to_dict()
    else:
        return result


def _do_create_internal(
    content: str | None = None,
    title: str | None = None,
    doc_type: str = "doc",
    folder_id: str | None = None,
    source: Path | None = None,
) -> CreateResult | CreateError:
    """Internal create logic — keeps existing CreateResult/CreateError pattern."""
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

    # Check for multi-tab sheet deposit (source with tabs in manifest)
    multi_tab_data: list[tuple[str, str]] | None = None
    if source and doc_type == "sheet":
        manifest = _read_manifest(source)
        if manifest.get("tabs"):
            try:
                multi_tab_data = _read_multi_tab_source(source)
            except MiseError as e:
                return CreateError(kind=e.kind.value, message=e.message)
            if not title:
                title = manifest.get("title")

    if source and not multi_tab_data:
        try:
            source_content, manifest_title = _read_source(source, doc_type)
        except MiseError as e:
            return CreateError(kind=e.kind.value, message=e.message)
        content = source_content
        if not title:
            title = manifest_title

    if not multi_tab_data and not content:
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
        elif doc_type == "sheet" and multi_tab_data:
            result = _create_multi_tab_sheet(multi_tab_data, title, folder_id)
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


def _create_multi_tab_sheet(
    tabs: list[tuple[str, str]],
    title: str,
    folder_id: str | None = None,
) -> CreateResult | CreateError:
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
        return CreateError(kind="invalid_input", message="No tabs provided for multi-tab sheet.")

    first_tab_name, first_tab_csv = tabs[0]

    # Step 1: CSV upload creates the spreadsheet with tab 1
    result = _create_sheet(first_tab_csv, title, folder_id)
    if isinstance(result, CreateError):
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
