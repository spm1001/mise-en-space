"""
Fetch tool implementation.

Routes by ID type, extracts content, deposits to workspace.
"""

from adapters.drive import get_file_metadata, GOOGLE_DOC_MIME, GOOGLE_SHEET_MIME, GOOGLE_SLIDES_MIME
from adapters.gmail import fetch_thread
from adapters.docs import fetch_document
from adapters.sheets import fetch_spreadsheet
from adapters.slides import fetch_presentation
from adapters.genai import get_video_summary, is_media_file
from adapters.cdp import is_cdp_available
from adapters.pdf import fetch_and_extract_pdf
from adapters.office import fetch_and_extract_office, get_office_type_from_mime, OfficeType
from extractors.docs import extract_doc_content
from extractors.sheets import extract_sheets_content
from extractors.slides import extract_slides_content
from extractors.gmail import extract_thread_content
from typing import Any, Literal
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


def fetch_gmail(thread_id: str) -> FetchResult:
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
    extra: dict[str, Any] = {"message_count": len(thread_data.messages)}
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


def fetch_drive(file_id: str) -> FetchResult | FetchError:
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
    elif is_media_file(mime_type):
        return fetch_video(file_id, title, metadata)
    elif mime_type == "application/pdf":
        return fetch_pdf(file_id, title, metadata)
    elif (office_type := get_office_type_from_mime(mime_type)):
        return fetch_office(file_id, title, metadata, office_type)
    else:
        # For now, return error for unsupported types
        return FetchError(
            kind="unsupported_type",
            message=f"Unsupported file type: {mime_type}",
            file_id=file_id,
            name=title,
        )


def fetch_doc(doc_id: str, title: str, metadata: dict[str, Any]) -> FetchResult:
    """Fetch Google Doc."""
    doc_data = fetch_document(doc_id)
    content = extract_doc_content(doc_data)

    folder = get_deposit_folder("doc", title, doc_id)
    content_path = write_content(folder, content)
    extra: dict[str, Any] = {"tab_count": len(doc_data.tabs) if doc_data.tabs else 1}
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


def fetch_sheet(sheet_id: str, title: str, metadata: dict[str, Any]) -> FetchResult:
    """Fetch Google Sheet."""
    sheet_data = fetch_spreadsheet(sheet_id)
    content = extract_sheets_content(sheet_data)

    folder = get_deposit_folder("sheet", title, sheet_id)
    content_path = write_content(folder, content, filename="content.csv")
    extra: dict[str, Any] = {"sheet_count": len(sheet_data.sheets)}
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


def fetch_slides(presentation_id: str, title: str, metadata: dict[str, Any]) -> FetchResult:
    """Fetch Google Slides."""
    # Enable thumbnails - selective logic in adapter skips stock photos/text-only
    presentation_data = fetch_presentation(presentation_id, include_thumbnails=True)
    content = extract_slides_content(presentation_data)

    folder = get_deposit_folder("slides", title, presentation_id)
    content_path = write_content(folder, content)

    # Write thumbnails if available, track failures
    thumbnail_count = 0
    thumbnail_failures: list[int] = []
    for slide in presentation_data.slides:
        if slide.thumbnail_bytes:
            write_thumbnail(folder, slide.thumbnail_bytes, slide.index)
            thumbnail_count += 1
        elif slide.needs_thumbnail:
            # Thumbnail was requested but not received
            thumbnail_failures.append(slide.index + 1)  # 1-indexed for humans

    extra: dict[str, Any] = {
        "slide_count": len(presentation_data.slides),
        "has_thumbnails": thumbnail_count > 0,
        "thumbnail_count": thumbnail_count,
    }
    if thumbnail_failures:
        extra["thumbnail_failures"] = thumbnail_failures
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


def fetch_video(file_id: str, title: str, metadata: dict[str, Any]) -> FetchResult:
    """
    Fetch video/audio file with AI summary if available.

    Tries to get pre-computed AI summary via GenAI API (requires chrome-debug).
    Falls back to basic metadata if CDP not available.
    """
    mime_type = metadata.get("mimeType", "")
    duration_ms = metadata.get("videoMediaMetadata", {}).get("durationMillis")

    # Try to get AI summary
    summary_result = get_video_summary(file_id)

    # Build content
    content_lines = [f"# {title}", ""]

    if summary_result and summary_result.has_content:
        content_lines.append("## AI Summary")
        content_lines.append("")
        if summary_result.summary:
            content_lines.append(summary_result.summary)
            content_lines.append("")

        if summary_result.transcript_snippets:
            content_lines.append("## Transcript Snippets")
            content_lines.append("")
            for snippet in summary_result.transcript_snippets:
                content_lines.append(f"- {snippet}")
            content_lines.append("")
    elif summary_result and summary_result.error == "stale_cookies":
        content_lines.append("*AI summary unavailable — browser session expired.*")
        content_lines.append("")
        content_lines.append(
            "_Tip: Refresh your Google session in chrome-debug, then retry._"
        )
        content_lines.append("")
    elif summary_result and summary_result.error == "permission_denied":
        content_lines.append("*AI summary unavailable — no access to this video.*")
        content_lines.append("")
    else:
        content_lines.append("*No AI summary available.*")
        content_lines.append("")
        if not is_cdp_available():
            content_lines.append(
                "_Tip: Run `chrome-debug` to enable AI summaries for videos._"
            )
        content_lines.append("")

    # Add metadata section
    content_lines.append("## Metadata")
    content_lines.append("")
    content_lines.append(f"- **Type:** {mime_type}")
    if duration_ms:
        duration_s = int(duration_ms) // 1000
        minutes, seconds = divmod(duration_s, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            content_lines.append(f"- **Duration:** {hours}:{minutes:02d}:{seconds:02d}")
        else:
            content_lines.append(f"- **Duration:** {minutes}:{seconds:02d}")
    content_lines.append(f"- **Link:** {metadata.get('webViewLink', '')}")

    content = "\n".join(content_lines)

    # Deposit to workspace
    folder = get_deposit_folder("video", title, file_id)
    content_path = write_content(folder, content)

    extra = {
        "mime_type": mime_type,
        "has_summary": summary_result.has_content if summary_result else False,
    }
    if duration_ms:
        extra["duration_ms"] = int(duration_ms)
    write_manifest(folder, "video", title, file_id, extra=extra)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="video",
        metadata={
            "title": title,
            "mime_type": mime_type,
            "has_summary": summary_result.has_content if summary_result else False,
        },
    )


def fetch_pdf(file_id: str, title: str, metadata: dict[str, Any]) -> FetchResult:
    """
    Fetch PDF file with hybrid extraction strategy.

    Uses adapters/pdf.py which tries markitdown first, falls back to Drive
    conversion for complex/image-heavy PDFs.
    """
    # Extract via adapter (handles download + hybrid extraction)
    result = fetch_and_extract_pdf(file_id)

    # Deposit to workspace
    folder = get_deposit_folder("pdf", title, file_id)
    content_path = write_content(folder, result.content)

    extra: dict[str, Any] = {
        "char_count": result.char_count,
        "extraction_method": result.method,
    }
    if result.warnings:
        extra["warnings"] = result.warnings
    write_manifest(folder, "pdf", title, file_id, extra=extra)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="pdf",
        metadata={
            "title": title,
            "extraction_method": result.method,
        },
    )


def fetch_office(file_id: str, title: str, metadata: dict[str, Any], office_type: OfficeType) -> FetchResult:
    """
    Fetch Office file via Drive conversion.

    Uses adapters/office.py which handles download, conversion, and cleanup.
    """
    # Extract via adapter (handles download + conversion)
    result = fetch_and_extract_office(file_id, office_type)

    # Determine output format
    output_format = "csv" if office_type == "xlsx" else "markdown"
    filename = f"content.{result.extension}"

    # Deposit to workspace
    folder = get_deposit_folder(office_type, title, file_id)
    content_path = write_content(folder, result.content, filename=filename)

    extra_office: dict[str, Any] = {}
    if result.warnings:
        extra_office["warnings"] = result.warnings
    write_manifest(folder, office_type, title, file_id, extra=extra_office if extra_office else None)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format=output_format,
        type=office_type,
        metadata={
            "title": title,
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
