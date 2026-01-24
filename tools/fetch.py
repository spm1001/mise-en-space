"""
Fetch tool implementation.

Routes by ID type, extracts content, deposits to workspace.
"""

import tempfile
from pathlib import Path

from googleapiclient.http import MediaInMemoryUpload
from markitdown import MarkItDown

from adapters.drive import get_file_metadata, download_file, GOOGLE_DOC_MIME, GOOGLE_SHEET_MIME, GOOGLE_SLIDES_MIME
from adapters.services import get_drive_service
from adapters.gmail import fetch_thread
from adapters.docs import fetch_document
from adapters.sheets import fetch_spreadsheet
from adapters.slides import fetch_presentation
from adapters.genai import get_video_summary, is_media_file
from adapters.cdp import is_cdp_available
from extractors.docs import extract_doc_content
from extractors.sheets import extract_sheets_content
from extractors.slides import extract_slides_content
from extractors.gmail import extract_thread_content
from models import MiseError, FetchResult, FetchError
from validation import extract_drive_file_id, extract_gmail_id, is_gmail_api_id
from workspace import get_deposit_folder, write_content, write_manifest, write_thumbnail


# Threshold for markitdown fallback to Drive conversion.
# If markitdown extracts less than this many chars, assume it failed on a complex/image PDF.
# Determined empirically: simple PDFs produce 1000s of chars, complex ones produce <100.
MARKITDOWN_MIN_CHARS = 500


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
    elif is_media_file(mime_type):
        return fetch_video(file_id, title, metadata)
    elif mime_type == "application/pdf":
        return fetch_pdf(file_id, title, metadata)
    elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return fetch_office(file_id, title, metadata, "docx")
    elif mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return fetch_office(file_id, title, metadata, "xlsx")
    elif mime_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        return fetch_office(file_id, title, metadata, "pptx")
    else:
        # For now, return error for unsupported types
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


def fetch_video(file_id: str, title: str, metadata: dict) -> dict:
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


def fetch_pdf(file_id: str, title: str, metadata: dict) -> dict:
    """
    Fetch PDF file with hybrid extraction strategy.

    1. Try markitdown first (fast, ~1-5s)
    2. If <500 chars extracted, fall back to Drive conversion (slower, ~10-20s)

    Drive conversion handles complex/image-heavy PDFs that markitdown can't parse.
    This gives fast results for simple PDFs while ensuring quality on complex ones.
    """
    # 1. Download the PDF
    pdf_bytes = download_file(file_id)

    # 2. Try markitdown first (fast path)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = Path(tmp.name)

    try:
        md = MarkItDown()
        result = md.convert_local(str(tmp_path))
        content = result.text_content or ""
    finally:
        tmp_path.unlink(missing_ok=True)

    # 3. If markitdown barfed (<500 chars), fall back to Drive conversion
    used_drive = False
    if len(content.strip()) < MARKITDOWN_MIN_CHARS:
        service = get_drive_service()
        media = MediaInMemoryUpload(pdf_bytes, mimetype="application/pdf")
        uploaded = service.files().create(
            body={"name": f"_mise_temp_{file_id}", "mimeType": "application/vnd.google-apps.document"},
            media_body=media,
            fields="id",
        ).execute()
        temp_id = uploaded["id"]

        try:
            content = service.files().export(
                fileId=temp_id,
                mimeType="text/markdown",
            ).execute()

            if isinstance(content, bytes):
                content = content.decode("utf-8")
            used_drive = True

        finally:
            try:
                service.files().delete(fileId=temp_id).execute()
            except Exception:
                pass

    # 4. Deposit to workspace
    folder = get_deposit_folder("pdf", title, file_id)
    content_path = write_content(folder, content)

    extra = {"file_size": len(pdf_bytes), "extraction_method": "drive" if used_drive else "markitdown"}
    write_manifest(folder, "pdf", title, file_id, extra=extra)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="pdf",
        metadata={
            "title": title,
            "file_size": len(pdf_bytes),
            "extraction_method": "drive" if used_drive else "markitdown",
        },
    )


# Office format mappings: source MIME → (Google MIME, export MIME, export extension)
OFFICE_CONVERSIONS = {
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.google-apps.document",
        "text/markdown",
        "md",
    ),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.google-apps.spreadsheet",
        "text/csv",
        "csv",
    ),
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.google-apps.presentation",
        "text/plain",
        "txt",
    ),
}


def fetch_office(file_id: str, title: str, metadata: dict, office_type: str) -> dict:
    """
    Fetch Office file via Drive conversion.

    Downloads file, uploads with conversion to Google format, exports, then deletes temp.
    This produces cleaner output than local conversion (markitdown), especially for XLSX.
    """
    source_mime, google_mime, export_mime, ext = OFFICE_CONVERSIONS[office_type]
    service = get_drive_service()

    # 1. Download the Office file
    file_bytes = download_file(file_id)

    # 2. Upload with conversion to Google format
    media = MediaInMemoryUpload(file_bytes, mimetype=source_mime)
    uploaded = service.files().create(
        body={"name": f"_mise_temp_{file_id}", "mimeType": google_mime},
        media_body=media,
        fields="id",
    ).execute()
    temp_id = uploaded["id"]

    try:
        # 3. Export from Google format
        content = service.files().export(
            fileId=temp_id,
            mimeType=export_mime,
        ).execute()

        # Decode if bytes
        if isinstance(content, bytes):
            content = content.decode("utf-8")

    finally:
        # 4. Always delete the temp file
        try:
            service.files().delete(fileId=temp_id).execute()
        except Exception:
            pass  # Best effort cleanup

    # Determine output format
    output_format = "csv" if office_type == "xlsx" else "markdown"
    filename = f"content.{ext}"

    # Deposit to workspace
    folder = get_deposit_folder(office_type, title, file_id)
    content_path = write_content(folder, content, filename=filename)

    extra = {"file_size": len(file_bytes)}
    write_manifest(folder, office_type, title, file_id, extra=extra)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format=output_format,
        type=office_type,
        metadata={
            "title": title,
            "file_size": len(file_bytes),
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
