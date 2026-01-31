"""
Fetch tool implementation.

Routes by ID type, extracts content, deposits to workspace.
"""

from adapters.drive import get_file_metadata, _parse_email_context, download_file, GOOGLE_DOC_MIME, GOOGLE_SHEET_MIME, GOOGLE_SLIDES_MIME, fetch_file_comments
from adapters.gmail import fetch_thread, download_attachment
from adapters.docs import fetch_document
from adapters.sheets import fetch_spreadsheet
from adapters.slides import fetch_presentation
from adapters.genai import get_video_summary, is_media_file
from adapters.cdp import is_cdp_available
from adapters.pdf import fetch_and_extract_pdf
from adapters.office import fetch_and_extract_office, get_office_type_from_mime, OfficeType
from adapters.image import fetch_image as adapter_fetch_image, is_image_file, is_svg
from extractors.docs import extract_doc_content
from extractors.sheets import extract_sheets_content
from extractors.slides import extract_slides_content
from extractors.gmail import extract_thread_content
from extractors.comments import extract_comments_content
from typing import Any, Literal
from models import MiseError, FetchResult, FetchError, EmailContext
from validation import extract_drive_file_id, extract_gmail_id, is_gmail_api_id
from workspace import get_deposit_folder, write_content, write_manifest, write_thumbnail, write_image, write_chart, write_charts_metadata


def _get_open_comment_count(file_id: str) -> int | None:
    """
    Get count of open (unresolved) comments on a file.

    Returns None if comments not supported for this file type.
    Fails silently — comment count is optional metadata.
    """
    try:
        data = fetch_file_comments(file_id, include_resolved=False, max_results=100)
        return data.comment_count
    except MiseError:
        return None
    except Exception:
        return None


# Text MIME types that can be downloaded and deposited directly
TEXT_MIME_TYPES = {
    "text/plain",
    "text/csv",
    "text/markdown",
    "text/html",
    "text/xml",
    "application/json",
    "application/xml",
    "application/x-yaml",
    "text/x-python",
    "text/javascript",
    "application/javascript",
}


def is_text_file(mime_type: str) -> bool:
    """Check if MIME type is a text-based format we can handle directly."""
    if mime_type in TEXT_MIME_TYPES:
        return True
    # Also handle any text/* type not explicitly listed
    if mime_type.startswith("text/"):
        return True
    return False


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


# MIME types for Office files that are too slow to extract eagerly (5-10s each)
OFFICE_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # pptx
    "application/msword",  # doc
    "application/vnd.ms-excel",  # xls
    "application/vnd.ms-powerpoint",  # ppt
}

# Maximum attachments to extract eagerly (prevent runaway extraction)
MAX_EAGER_ATTACHMENTS = 10


def _is_extractable_attachment(mime_type: str) -> bool:
    """
    Check if attachment MIME type is extractable.

    Office files are skipped (too slow for eager extraction).
    PDFs and images are extracted.
    """
    # Skip Office files - they take 5-10s each
    if mime_type in OFFICE_MIME_TYPES:
        return False

    # Extract PDFs
    if mime_type == "application/pdf":
        return True

    # Extract images (will be deposited as-is)
    if mime_type.startswith("image/"):
        return True

    return False


def _extract_attachment_content(
    message_id: str,
    att: Any,  # EmailAttachment
    folder: Any,  # Path
    warnings: list[str],
) -> dict[str, Any] | None:
    """
    Download and extract content from a single attachment.

    Returns extraction result dict or None on failure.
    """
    try:
        # Download attachment
        download = download_attachment(
            message_id=message_id,
            attachment_id=att.attachment_id,
            filename=att.filename,
            mime_type=att.mime_type,
        )

        # Route by MIME type
        if att.mime_type == "application/pdf":
            # Extract PDF text via adapter
            from adapters.pdf import extract_pdf_content
            result = extract_pdf_content(download.content, file_id=att.attachment_id)

            # Write extracted content to folder
            content_filename = f"{att.filename}.md"
            write_content(folder, result.content, filename=content_filename)

            # Also write raw PDF for reference
            write_image(folder, download.content, att.filename)

            return {
                "filename": att.filename,
                "mime_type": att.mime_type,
                "extracted": True,
                "extraction_method": result.method,
                "content_file": content_filename,
                "char_count": result.char_count,
            }

        elif att.mime_type.startswith("image/"):
            # Deposit image as-is (Claude can view images)
            write_image(folder, download.content, att.filename)

            return {
                "filename": att.filename,
                "mime_type": att.mime_type,
                "extracted": True,
                "deposited_as": att.filename,
            }

        # Clean up temp file if created
        if download.temp_path:
            download.temp_path.unlink(missing_ok=True)

        return None

    except Exception as e:
        warnings.append(f"Failed to extract {att.filename}: {str(e)}")
        return None


def fetch_gmail(thread_id: str) -> FetchResult:
    """
    Fetch Gmail thread, extract content and attachments, deposit to workspace.

    Eager extraction philosophy: By the time Claude calls fetch, they've
    committed to reading this conversation. PDFs and images are part of
    what they want - extract immediately.

    Office files (DOCX/XLSX/PPTX) are skipped due to slow extraction (5-10s each).
    They're listed in metadata so Claude can fetch explicitly if needed.
    """
    # Fetch thread data
    thread_data = fetch_thread(thread_id)

    # Extract thread text content
    content = extract_thread_content(thread_data)

    # Get deposit folder early (need it for attachment extraction)
    folder = get_deposit_folder(
        content_type="gmail",
        title=thread_data.subject or "email-thread",
        resource_id=thread_id,
    )

    # Collect attachments and drive_links from all messages
    all_attachments: list[dict[str, Any]] = []
    all_drive_links: list[dict[str, str]] = []
    skipped_office: list[str] = []
    extracted_attachments: list[dict[str, Any]] = []
    extraction_warnings: list[str] = []
    extracted_count = 0

    for msg in thread_data.messages:
        for att in msg.attachments:
            att_info = {
                "filename": att.filename,
                "mime_type": att.mime_type,
                "size": att.size,
            }
            all_attachments.append(att_info)

            # Skip Office files (note for manifest)
            if att.mime_type in OFFICE_MIME_TYPES:
                skipped_office.append(att.filename)
                continue

            # Limit eager extraction
            if extracted_count >= MAX_EAGER_ATTACHMENTS:
                extraction_warnings.append(
                    f"Attachment limit ({MAX_EAGER_ATTACHMENTS}) reached, "
                    f"skipping: {att.filename}"
                )
                continue

            # Try to extract extractable types
            if _is_extractable_attachment(att.mime_type):
                result = _extract_attachment_content(
                    message_id=msg.message_id,
                    att=att,
                    folder=folder,
                    warnings=extraction_warnings,
                )
                if result:
                    extracted_attachments.append(result)
                    extracted_count += 1

        all_drive_links.extend(msg.drive_links)

    # Write thread content
    content_path = write_content(folder, content)

    # Build manifest extras
    extra: dict[str, Any] = {"message_count": len(thread_data.messages)}
    if thread_data.warnings:
        extra["warnings"] = thread_data.warnings + extraction_warnings
    elif extraction_warnings:
        extra["warnings"] = extraction_warnings
    if extracted_attachments:
        extra["extracted_attachments"] = len(extracted_attachments)
    if skipped_office:
        extra["skipped_office"] = skipped_office

    write_manifest(
        folder,
        content_type="gmail",
        title=thread_data.subject or "email-thread",
        resource_id=thread_id,
        extra=extra,
    )

    # Build result metadata
    metadata: dict[str, Any] = {
        "subject": thread_data.subject,
        "message_count": len(thread_data.messages),
    }
    if all_attachments:
        metadata["attachments"] = all_attachments
    if all_drive_links:
        metadata["drive_links"] = all_drive_links
    if extracted_attachments:
        metadata["extracted"] = extracted_attachments
    if skipped_office:
        metadata["skipped_office"] = skipped_office
        metadata["skipped_office_hint"] = (
            "Office files take 5-10s each to extract. "
            "Fetch individually if needed: fetch('message_id', attachment='filename')"
        )

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="gmail",
        metadata=metadata,
    )


def fetch_drive(file_id: str) -> FetchResult | FetchError:
    """Fetch Drive file, route by type, extract content, deposit to workspace."""
    # Get metadata to determine type
    metadata = get_file_metadata(file_id)
    mime_type = metadata.get("mimeType", "")
    title = metadata.get("name", "untitled")

    # Parse email context for cross-source linkage (exfil'd files)
    email_context = _parse_email_context(metadata.get("description"))

    # Route by MIME type
    if mime_type == GOOGLE_DOC_MIME:
        return fetch_doc(file_id, title, metadata, email_context)
    elif mime_type == GOOGLE_SHEET_MIME:
        return fetch_sheet(file_id, title, metadata, email_context)
    elif mime_type == GOOGLE_SLIDES_MIME:
        return fetch_slides(file_id, title, metadata, email_context)
    elif is_media_file(mime_type):
        return fetch_video(file_id, title, metadata, email_context)
    elif mime_type == "application/pdf":
        return fetch_pdf(file_id, title, metadata, email_context)
    elif (office_type := get_office_type_from_mime(mime_type)):
        return fetch_office(file_id, title, metadata, office_type, email_context)
    elif is_text_file(mime_type):
        return fetch_text(file_id, title, metadata, email_context)
    elif is_image_file(mime_type):
        return fetch_image_file(file_id, title, metadata, email_context)
    else:
        # Return error for unsupported types
        return FetchError(
            kind="unsupported_type",
            message=f"Unsupported file type: {mime_type}",
            file_id=file_id,
            name=title,
        )


def _build_email_context_metadata(email_context: EmailContext | None) -> dict[str, Any] | None:
    """Build email_context dict for FetchResult metadata."""
    if not email_context:
        return None
    return {
        "message_id": email_context.message_id,
        "from": email_context.from_address,
        "subject": email_context.subject,
        "hint": f"Use fetch('{email_context.message_id}') to get source email",
    }


def fetch_doc(doc_id: str, title: str, metadata: dict[str, Any], email_context: EmailContext | None = None) -> FetchResult:
    """Fetch Google Doc."""
    doc_data = fetch_document(doc_id)
    content = extract_doc_content(doc_data)

    folder = get_deposit_folder("doc", title, doc_id)
    content_path = write_content(folder, content)
    extra: dict[str, Any] = {"tab_count": len(doc_data.tabs) if doc_data.tabs else 1}
    if doc_data.warnings:
        extra["warnings"] = doc_data.warnings
    # Add open comment count (optional, fails silently)
    open_comments = _get_open_comment_count(doc_id)
    if open_comments is not None:
        extra["open_comment_count"] = open_comments
    write_manifest(folder, "doc", title, doc_id, extra=extra)

    result_metadata: dict[str, Any] = {"title": title, "mimeType": metadata.get("mimeType")}
    if email_context:
        result_metadata["email_context"] = _build_email_context_metadata(email_context)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="doc",
        metadata=result_metadata,
    )


def fetch_sheet(sheet_id: str, title: str, metadata: dict[str, Any], email_context: EmailContext | None = None) -> FetchResult:
    """Fetch Google Sheet with charts rendered as PNGs."""
    sheet_data = fetch_spreadsheet(sheet_id)
    content = extract_sheets_content(sheet_data)

    folder = get_deposit_folder("sheet", title, sheet_id)
    content_path = write_content(folder, content, filename="content.csv")

    # Write chart PNGs
    chart_count = 0
    charts_meta: list[dict[str, Any]] = []
    for i, chart in enumerate(sheet_data.charts):
        if chart.png_bytes:
            write_chart(folder, chart.png_bytes, i)
            chart_count += 1

        # Always include metadata even if PNG failed
        charts_meta.append({
            "chart_id": chart.chart_id,
            "title": chart.title,
            "sheet_name": chart.sheet_name,
            "chart_type": chart.chart_type,
            "has_png": chart.png_bytes is not None,
        })

    # Write charts.json if there are charts
    if charts_meta:
        write_charts_metadata(folder, charts_meta)

    # Build manifest extras
    extra: dict[str, Any] = {"sheet_count": len(sheet_data.sheets)}
    if chart_count > 0:
        extra["chart_count"] = chart_count
        extra["chart_render_time_ms"] = sheet_data.chart_render_time_ms
    if sheet_data.warnings:
        extra["warnings"] = sheet_data.warnings
    # Add open comment count (optional, fails silently)
    open_comments = _get_open_comment_count(sheet_id)
    if open_comments is not None:
        extra["open_comment_count"] = open_comments
    write_manifest(folder, "sheet", title, sheet_id, extra=extra)

    result_meta: dict[str, Any] = {
        "title": title,
        "sheet_count": len(sheet_data.sheets),
    }
    if chart_count > 0:
        result_meta["chart_count"] = chart_count
        result_meta["chart_render_time_ms"] = sheet_data.chart_render_time_ms
    if email_context:
        result_meta["email_context"] = _build_email_context_metadata(email_context)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="csv",
        type="sheet",
        metadata=result_meta,
    )


def fetch_slides(presentation_id: str, title: str, metadata: dict[str, Any], email_context: EmailContext | None = None) -> FetchResult:
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
    # Add open comment count (optional, fails silently)
    open_comments = _get_open_comment_count(presentation_id)
    if open_comments is not None:
        extra["open_comment_count"] = open_comments
    write_manifest(folder, "slides", title, presentation_id, extra=extra)

    result_meta: dict[str, Any] = {
        "title": title,
        "slide_count": len(presentation_data.slides),
        "thumbnail_count": thumbnail_count,
    }
    if email_context:
        result_meta["email_context"] = _build_email_context_metadata(email_context)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="slides",
        metadata=result_meta,
    )


def fetch_video(file_id: str, title: str, metadata: dict[str, Any], email_context: EmailContext | None = None) -> FetchResult:
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

    result_meta: dict[str, Any] = {
        "title": title,
        "mime_type": mime_type,
        "has_summary": summary_result.has_content if summary_result else False,
    }
    if email_context:
        result_meta["email_context"] = _build_email_context_metadata(email_context)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="video",
        metadata=result_meta,
    )


def fetch_pdf(file_id: str, title: str, metadata: dict[str, Any], email_context: EmailContext | None = None) -> FetchResult:
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

    result_meta: dict[str, Any] = {
        "title": title,
        "extraction_method": result.method,
    }
    if email_context:
        result_meta["email_context"] = _build_email_context_metadata(email_context)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="pdf",
        metadata=result_meta,
    )


def fetch_office(file_id: str, title: str, metadata: dict[str, Any], office_type: OfficeType, email_context: EmailContext | None = None) -> FetchResult:
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

    result_meta: dict[str, Any] = {
        "title": title,
    }
    if email_context:
        result_meta["email_context"] = _build_email_context_metadata(email_context)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format=output_format,
        type=office_type,
        metadata=result_meta,
    )


def fetch_text(file_id: str, title: str, metadata: dict[str, Any], email_context: EmailContext | None = None) -> FetchResult:
    """
    Fetch text-based file (txt, csv, json, etc.) by downloading directly.

    No extraction needed — just download and deposit.
    """
    mime_type = metadata.get("mimeType", "text/plain")

    # Download content
    content_bytes = download_file(file_id)
    content = content_bytes.decode("utf-8", errors="replace")

    # Determine output format and extension
    extension_map = {
        "text/csv": ("csv", "csv"),
        "application/json": ("json", "json"),
        "text/markdown": ("markdown", "md"),
        "text/html": ("html", "html"),
        "text/xml": ("xml", "xml"),
        "application/xml": ("xml", "xml"),
        "application/x-yaml": ("yaml", "yaml"),
    }
    output_format, ext = extension_map.get(mime_type, ("text", "txt"))
    filename = f"content.{ext}"

    # Deposit to workspace
    folder = get_deposit_folder("text", title, file_id)
    content_path = write_content(folder, content, filename=filename)

    extra: dict[str, Any] = {
        "mime_type": mime_type,
        "char_count": len(content),
    }
    write_manifest(folder, "text", title, file_id, extra=extra)

    result_meta: dict[str, Any] = {
        "title": title,
        "mime_type": mime_type,
        "char_count": len(content),
    }
    if email_context:
        result_meta["email_context"] = _build_email_context_metadata(email_context)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format=output_format,
        type="text",
        metadata=result_meta,
    )


def fetch_image_file(file_id: str, title: str, metadata: dict[str, Any], email_context: EmailContext | None = None) -> FetchResult:
    """
    Fetch image file (PNG, JPEG, GIF, WEBP, SVG, etc.).

    For raster images: deposit as-is.
    For SVG: deposit raw SVG + render to PNG (Claude can view PNGs but not SVGs).
    """
    mime_type = metadata.get("mimeType", "")

    # Fetch via adapter (handles download + SVG rendering)
    result = adapter_fetch_image(file_id, title, mime_type)

    # Deposit to workspace
    folder = get_deposit_folder("image", title, file_id)

    # Write the original image
    image_path = write_image(folder, result.image_bytes, result.filename)

    # For SVG, also write rendered PNG if available
    rendered_png_filename = None
    if result.rendered_png_bytes:
        rendered_png_filename = "image_rendered.png"
        write_image(folder, result.rendered_png_bytes, rendered_png_filename)

    # Build manifest extras
    extra: dict[str, Any] = {
        "mime_type": mime_type,
        "size_bytes": len(result.image_bytes),
    }
    if is_svg(mime_type):
        extra["is_svg"] = True
        if result.render_method:
            extra["render_method"] = result.render_method
            extra["has_rendered_png"] = True
        else:
            extra["has_rendered_png"] = False
    if result.warnings:
        extra["warnings"] = result.warnings

    write_manifest(folder, "image", title, file_id, extra=extra)

    # Build result metadata
    result_meta: dict[str, Any] = {
        "title": title,
        "mime_type": mime_type,
        "size_bytes": len(result.image_bytes),
    }
    if is_svg(mime_type):
        result_meta["is_svg"] = True
        result_meta["has_rendered_png"] = result.rendered_png_bytes is not None
        if result.render_method:
            result_meta["render_method"] = result.render_method
    if email_context:
        result_meta["email_context"] = _build_email_context_metadata(email_context)

    # Content file is the original image (or rendered PNG for SVG if available)
    content_file = str(folder / rendered_png_filename) if rendered_png_filename else str(image_path)

    return FetchResult(
        path=str(folder),
        content_file=content_file,
        format="image",
        type="image",
        metadata=result_meta,
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


def do_fetch_comments(
    file_id: str,
    include_deleted: bool = False,
    include_resolved: bool = True,
    max_results: int = 100,
) -> dict[str, Any]:
    """
    Fetch comments from a Drive file.

    Returns comments as formatted markdown directly (no file deposit).
    Comments are typically small enough to return inline.

    Args:
        file_id: Drive file ID or URL
        include_deleted: Include deleted comments
        include_resolved: Include resolved comments (default: True).
            Set to False to get only unresolved/open comments.
        max_results: Maximum comments to fetch

    Returns:
        Dict with:
        - content: Formatted markdown string
        - file_id: The file ID
        - file_name: The file name
        - comment_count: Number of comments
        - warnings: Any extraction warnings
    """
    try:
        # Normalize file ID (handle URLs)
        _, normalized_id = detect_id_type(file_id)

        # Fetch comments via adapter
        data = fetch_file_comments(
            file_id=normalized_id,
            include_deleted=include_deleted,
            include_resolved=include_resolved,
            max_results=max_results,
        )

        # Extract to markdown
        content = extract_comments_content(data)

        return {
            "content": content,
            "file_id": data.file_id,
            "file_name": data.file_name,
            "comment_count": data.comment_count,
            "warnings": data.warnings if data.warnings else None,
        }

    except MiseError as e:
        return {
            "error": True,
            "kind": e.kind.value,
            "message": e.message,
        }
    except Exception as e:
        return {
            "error": True,
            "kind": "unknown",
            "message": str(e),
        }
