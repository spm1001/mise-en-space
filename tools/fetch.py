"""
Fetch tool implementation.

Routes by ID type, extracts content, deposits to workspace.
"""

from adapters.drive import get_file_metadata, GOOGLE_DOC_MIME, GOOGLE_SHEET_MIME, GOOGLE_SLIDES_MIME
from adapters.gmail import fetch_thread
from adapters.docs import fetch_document
from adapters.sheets import fetch_spreadsheet
from adapters.slides import fetch_presentation
from extractors.docs import extract_doc_content
from extractors.sheets import extract_sheets_content
from extractors.slides import extract_slides_content
from extractors.gmail import extract_thread_content
from models import MiseError, FetchResult, FetchError
from validation import extract_drive_file_id, extract_gmail_id, is_gmail_api_id
from workspace import get_deposit_folder, write_content, write_manifest, write_thumbnail


def detect_id_type(input_id: str) -> tuple[str, str]:
    """
    Detect whether input is Gmail or Drive, and normalize the ID.

    Returns:
        Tuple of (source, normalized_id) where source is 'gmail' or 'drive'
    """
    input_id = input_id.strip()

    # Gmail URL
    if "mail.google.com" in input_id:
        return ("gmail", extract_gmail_id(input_id))

    # Drive URL (docs, sheets, slides, drive)
    if any(domain in input_id for domain in ["docs.google.com", "sheets.google.com", "slides.google.com", "drive.google.com"]):
        return ("drive", extract_drive_file_id(input_id))

    # Gmail API ID (16-char hex)
    if is_gmail_api_id(input_id):
        return ("gmail", input_id)

    # Default to Drive
    return ("drive", input_id)


def fetch_gmail(thread_id: str) -> dict:
    """Fetch Gmail thread, extract content, deposit to workspace."""
    # Fetch thread data
    thread_data = fetch_thread(thread_id)

    # Extract content
    content = extract_thread_content(thread_data)

    # Deposit to workspace
    folder = get_deposit_folder(
        content_type="gmail",
        title=thread_data.subject or "email-thread",
        resource_id=thread_id,
    )
    content_path = write_content(folder, content)
    extra = {"message_count": len(thread_data.messages)}
    if thread_data.warnings:
        extra["warnings"] = thread_data.warnings
    write_manifest(
        folder,
        content_type="gmail",
        title=thread_data.subject or "email-thread",
        resource_id=thread_id,
        extra=extra,
    )

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="gmail",
        metadata={
            "subject": thread_data.subject,
            "message_count": len(thread_data.messages),
        },
    )


def fetch_drive(file_id: str) -> dict:
    """Fetch Drive file, route by type, extract content, deposit to workspace."""
    # Get metadata to determine type
    metadata = get_file_metadata(file_id)
    mime_type = metadata.get("mimeType", "")
    title = metadata.get("name", "untitled")

    # Route by MIME type
    if mime_type == GOOGLE_DOC_MIME:
        return fetch_doc(file_id, title, metadata)
    elif mime_type == GOOGLE_SHEET_MIME:
        return fetch_sheet(file_id, title, metadata)
    elif mime_type == GOOGLE_SLIDES_MIME:
        return fetch_slides(file_id, title, metadata)
    else:
        # For now, return error for unsupported types
        # TODO: Handle PDFs, Office files, binary downloads
        return FetchError(
            kind="unsupported_type",
            message=f"Unsupported file type: {mime_type}",
            file_id=file_id,
            name=title,
        )


def fetch_doc(doc_id: str, title: str, metadata: dict) -> dict:
    """Fetch Google Doc."""
    doc_data = fetch_document(doc_id)
    content = extract_doc_content(doc_data)

    folder = get_deposit_folder("doc", title, doc_id)
    content_path = write_content(folder, content)
    extra = {"tab_count": len(doc_data.tabs) if doc_data.tabs else 1}
    if doc_data.warnings:
        extra["warnings"] = doc_data.warnings
    write_manifest(folder, "doc", title, doc_id, extra=extra)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="doc",
        metadata={"title": title, "mimeType": metadata.get("mimeType")},
    )


def fetch_sheet(sheet_id: str, title: str, metadata: dict) -> dict:
    """Fetch Google Sheet."""
    sheet_data = fetch_spreadsheet(sheet_id)
    content = extract_sheets_content(sheet_data)

    folder = get_deposit_folder("sheet", title, sheet_id)
    content_path = write_content(folder, content, filename="content.csv")
    extra = {"sheet_count": len(sheet_data.sheets)}
    if sheet_data.warnings:
        extra["warnings"] = sheet_data.warnings
    write_manifest(folder, "sheet", title, sheet_id, extra=extra)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="csv",
        type="sheet",
        metadata={"title": title, "sheet_count": len(sheet_data.sheets)},
    )


def fetch_slides(presentation_id: str, title: str, metadata: dict) -> dict:
    """Fetch Google Slides."""
    presentation_data = fetch_presentation(presentation_id)
    content = extract_slides_content(presentation_data)

    folder = get_deposit_folder("slides", title, presentation_id)
    content_path = write_content(folder, content)

    # Write thumbnails if available
    thumbnail_count = 0
    for slide in presentation_data.slides:
        if slide.thumbnail_bytes:
            write_thumbnail(folder, slide.thumbnail_bytes, slide.index)
            thumbnail_count += 1

    extra = {
        "slide_count": len(presentation_data.slides),
        "has_thumbnails": thumbnail_count > 0,
        "thumbnail_count": thumbnail_count,
    }
    if presentation_data.warnings:
        extra["warnings"] = presentation_data.warnings
    write_manifest(folder, "slides", title, presentation_id, extra=extra)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="slides",
        metadata={
            "title": title,
            "slide_count": len(presentation_data.slides),
            "thumbnail_count": thumbnail_count,
        },
    )


def do_fetch(file_id: str) -> FetchResult | FetchError:
    """
    Main fetch entry point.

    Detects ID type, routes to appropriate fetcher, handles errors.
    """
    try:
        # Detect ID type and normalize
        source, normalized_id = detect_id_type(file_id)

        # Route to appropriate fetcher
        if source == "gmail":
            return fetch_gmail(normalized_id)
        else:
            return fetch_drive(normalized_id)

    except MiseError as e:
        return FetchError(kind=e.kind.value, message=e.message)
    except ValueError as e:
        return FetchError(kind="invalid_input", message=str(e))
    except Exception as e:
        return FetchError(kind="unknown", message=str(e))
