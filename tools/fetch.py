"""
Fetch tool implementation.

Routes by ID type, extracts content, deposits to workspace.
"""

import hashlib
from pathlib import Path
from urllib.parse import unquote, urlparse
from adapters.drive import get_file_metadata, _parse_email_context, download_file, GOOGLE_DOC_MIME, GOOGLE_SHEET_MIME, GOOGLE_SLIDES_MIME, fetch_file_comments, lookup_exfiltrated
from adapters.gmail import fetch_thread, download_attachment
from adapters.docs import fetch_document
from adapters.sheets import fetch_spreadsheet
from adapters.slides import fetch_presentation
from adapters.genai import get_video_summary, is_media_file
from adapters.cdp import is_cdp_available
from adapters.pdf import fetch_and_extract_pdf, extract_pdf_content
from adapters.office import fetch_and_extract_office, extract_office_content, get_office_type_from_mime, OfficeType, OfficeExtractionResult
from adapters.image import fetch_image as adapter_fetch_image, is_image_file, is_svg
from adapters.web import fetch_web_content, is_web_url
from extractors.docs import extract_doc_content
from extractors.sheets import extract_sheets_content
from extractors.slides import extract_slides_content
from extractors.gmail import extract_thread_content
from extractors.comments import extract_comments_content
from extractors.web import extract_web_content, extract_title
from typing import Any, Literal
from models import MiseError, ErrorKind, FetchResult, FetchError, EmailContext, WebData
from validation import extract_drive_file_id, extract_gmail_id, is_gmail_api_id, GMAIL_WEB_ID_PREFIXES
from workspace import get_deposit_folder, write_content, write_manifest, write_thumbnail, write_image, write_chart, write_charts_metadata


def _enrich_with_comments(file_id: str, folder: Path) -> tuple[int, str | None]:
    """
    Fetch open comments and write to deposit folder.

    Sous-chef philosophy: bring everything chef needs without being asked.

    Args:
        file_id: Drive file ID
        folder: Deposit folder path

    Returns:
        Tuple of (open_comment_count, comments_md or None)
        Fails silently — comments are optional enrichment.
    """
    try:
        data = fetch_file_comments(file_id, include_resolved=False, max_results=100)
        if not data.comments:
            return (0, None)

        # Extract to markdown
        comments_md = extract_comments_content(data)

        # Write to deposit folder
        write_content(folder, comments_md, filename="comments.md")

        return (data.comment_count, comments_md)
    except MiseError:
        return (0, None)
    except Exception:
        return (0, None)


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
    Detect whether input is Gmail, Drive, or web URL, and normalize the ID.

    Returns:
        Tuple of (source, normalized_id) where source is 'gmail', 'drive', or 'web'
    """
    input_id = input_id.strip()

    # Gmail URL
    if "mail.google.com" in input_id:
        return ("gmail", extract_gmail_id(input_id))

    # Drive URL (docs, sheets, slides, drive)
    if any(domain in input_id for domain in ["docs.google.com", "sheets.google.com", "slides.google.com", "drive.google.com"]):
        return ("drive", extract_drive_file_id(input_id))

    # Web URL (non-Google HTTP/HTTPS)
    if is_web_url(input_id):
        return ("web", input_id)

    # Gmail API ID (16-char hex)
    if is_gmail_api_id(input_id):
        return ("gmail", input_id)

    # Gmail web ID (FMfcg..., KtbxL..., etc.) — needs conversion
    # Only match known prefixes; is_gmail_web_id fallback is too broad for bare IDs
    if input_id.startswith(GMAIL_WEB_ID_PREFIXES):
        return ("gmail", extract_gmail_id(input_id))

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


def _match_exfil_file(
    att_filename: str,
    exfil_files: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Match Gmail attachment to pre-exfil'd Drive file with fallbacks.

    The exfil script may modify filenames:
    - ensureExtension: "report" → "report.pdf"
    - smartFilename: UUID names get date+sender prefix

    Strategies (in order):
    1. Exact name match
    2. Stem match (strip extensions, catches ensureExtension case)
    3. Single-file fallback (message ID already proved provenance)
    """
    if not exfil_files:
        return None

    # 1. Exact name match
    by_name = {f["name"]: f for f in exfil_files}
    if att_filename in by_name:
        return by_name[att_filename]

    # 2. Stem match — handles ensureExtension() adding .pdf/.xlsx etc.
    att_stem = att_filename.rsplit(".", 1)[0] if "." in att_filename else att_filename
    for f in exfil_files:
        drive_name = f["name"]
        drive_stem = drive_name.rsplit(".", 1)[0] if "." in drive_name else drive_name
        if att_stem == drive_stem:
            return f

    # 3. Single-file fallback — if only one exfil file for this message,
    # the message ID lookup already proved provenance
    if len(exfil_files) == 1:
        return exfil_files[0]

    return None


def _deposit_attachment_content(
    content_bytes: bytes,
    filename: str,
    mime_type: str,
    file_id: str,
    folder: Any,  # Path
) -> dict[str, Any] | None:
    """
    Route attachment bytes by MIME type and deposit to folder.

    Shared by both Drive (pre-exfil) and Gmail download paths.
    Returns extraction result dict or None if type not handled.
    """
    if mime_type == "application/pdf":
        from adapters.pdf import extract_pdf_content
        result = extract_pdf_content(content_bytes, file_id=file_id)

        content_filename = f"{filename}.md"
        write_content(folder, result.content, filename=content_filename)
        write_image(folder, content_bytes, filename)

        return {
            "filename": filename,
            "mime_type": mime_type,
            "extracted": True,
            "extraction_method": result.method,
            "content_file": content_filename,
            "char_count": result.char_count,
        }

    elif mime_type.startswith("image/"):
        write_image(folder, content_bytes, filename)

        return {
            "filename": filename,
            "mime_type": mime_type,
            "extracted": True,
            "deposited_as": filename,
        }

    return None


def _extract_from_drive(
    file_id: str,
    filename: str,
    mime_type: str,
    folder: Any,  # Path
    warnings: list[str],
) -> dict[str, Any] | None:
    """
    Extract content from a pre-exfiltrated Drive file.

    Faster when the file is already in Drive (background exfiltration).
    """
    try:
        content_bytes = download_file(file_id)
        return _deposit_attachment_content(content_bytes, filename, mime_type, file_id, folder)
    except Exception as e:
        warnings.append(f"Drive exfil fallback failed for {filename}: {str(e)}")
        return None


def _extract_attachment_content(
    message_id: str,
    att: Any,  # EmailAttachment
    folder: Any,  # Path
    warnings: list[str],
) -> dict[str, Any] | None:
    """
    Download and extract content from a single Gmail attachment.

    Returns extraction result dict or None on failure.
    """
    try:
        download = download_attachment(
            message_id=message_id,
            attachment_id=att.attachment_id,
            filename=att.filename,
            mime_type=att.mime_type,
        )

        result = _deposit_attachment_content(
            download.content, att.filename, att.mime_type, att.attachment_id, folder
        )

        # Clean up temp file if created and type not handled
        if result is None and download.temp_path:
            download.temp_path.unlink(missing_ok=True)

        return result

    except Exception as e:
        warnings.append(f"Failed to extract {att.filename}: {str(e)}")
        return None


def fetch_gmail(thread_id: str, base_path: Path | None = None) -> FetchResult:
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
        base_path=base_path,
    )

    # Collect attachments and drive_links from all messages
    all_attachments: list[dict[str, Any]] = []
    all_drive_links: list[dict[str, str]] = []
    skipped_office: list[str] = []
    extracted_attachments: list[dict[str, Any]] = []
    extraction_warnings: list[str] = []
    extracted_count = 0

    # Pre-exfil lookup: check if attachments already exist in Drive
    # (indexed by fullText, faster than Gmail download + extraction)
    message_ids = [msg.message_id for msg in thread_data.messages]
    exfiltrated = lookup_exfiltrated(message_ids)

    for msg in thread_data.messages:
        # Get Drive files matching this message's attachments
        exfil_files = exfiltrated.get(msg.message_id, [])

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

            # Try pre-exfil'd Drive copy first (faster, already indexed)
            exfil_match = _match_exfil_file(att.filename, exfil_files)
            if exfil_match and _is_extractable_attachment(att.mime_type):
                result = _extract_from_drive(
                    file_id=exfil_match["file_id"],
                    filename=att.filename,
                    mime_type=att.mime_type,
                    folder=folder,
                    warnings=extraction_warnings,
                )
                if result:
                    result["source"] = "drive_exfil"
                    extracted_attachments.append(result)
                    extracted_count += 1
                    continue

            # Fall back to Gmail download
            if _is_extractable_attachment(att.mime_type):
                result = _extract_attachment_content(
                    message_id=msg.message_id,
                    att=att,
                    folder=folder,
                    warnings=extraction_warnings,
                )
                if result:
                    result["source"] = "gmail"
                    extracted_attachments.append(result)
                    extracted_count += 1

        all_drive_links.extend(msg.drive_links)

    # Append extraction summary so caller knows which files were extracted
    if extracted_attachments:
        extraction_lines = ["\n---\n\n**Extracted attachments:**"]
        for att_result in extracted_attachments:
            content_file = att_result.get("content_file")
            if content_file:
                extraction_lines.append(
                    f"- {att_result['filename']} → `{content_file}`"
                )
            else:
                extraction_lines.append(
                    f"- {att_result['filename']} (deposited as file)"
                )
        content = content + "\n".join(extraction_lines) + "\n"

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
        examples = [f"fetch('{thread_id}', attachment='{f}')" for f in skipped_office]
        metadata["skipped_office_hint"] = (
            f"Office files take 5-10s each. "
            f"To extract: {'; '.join(examples)}"
        )

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="gmail",
        metadata=metadata,
    )


def _download_attachment_bytes(msg: Any, att: Any, mime_type: str) -> bytes:
    """Download attachment bytes from Gmail."""
    dl = download_attachment(
        message_id=msg.message_id,
        attachment_id=att.attachment_id,
        filename=att.filename,
        mime_type=mime_type,
    )
    return dl.content


def fetch_attachment(
    thread_id: str,
    attachment_name: str,
    base_path: Path | None = None,
) -> FetchResult | FetchError:
    """
    Fetch a single named attachment from a Gmail thread.

    Supports all extractable types including Office files (DOCX/XLSX/PPTX)
    that are skipped during eager thread extraction.
    """
    # 1. Fetch thread to find the attachment
    thread_data = fetch_thread(thread_id)

    # 2. Scan all messages for matching attachment
    target_att = None
    target_msg = None
    all_attachment_names: list[str] = []

    for msg in thread_data.messages:
        for att in msg.attachments:
            all_attachment_names.append(att.filename)
            if att.filename == attachment_name:
                target_att = att
                target_msg = msg
                break
        if target_att:
            break

    if not target_att or not target_msg:
        available = ", ".join(all_attachment_names) if all_attachment_names else "(none)"
        return FetchError(
            kind="not_found",
            message=f"No attachment named '{attachment_name}' in thread. Available: {available}",
        )

    mime_type = target_att.mime_type
    warnings: list[str] = []

    # 3. Check pre-exfil Drive copy
    exfil_file_id: str | None = None
    source_label = "gmail"

    try:
        exfiltrated = lookup_exfiltrated([target_msg.message_id])
        exfil_files = exfiltrated.get(target_msg.message_id, [])
        exfil_match = _match_exfil_file(target_att.filename, exfil_files)
        if exfil_match:
            exfil_file_id = exfil_match["file_id"]
            source_label = "drive_exfil"
    except Exception:
        pass  # Pre-exfil is optional optimization

    # 4. Route to extractor by MIME type and deposit
    title = attachment_name.rsplit(".", 1)[0] if "." in attachment_name else attachment_name

    # Office files (the primary use case for this feature)
    # Pre-exfil optimization: copy+convert in Drive without downloading
    office_type = get_office_type_from_mime(mime_type)
    if office_type:
        if exfil_file_id:
            try:
                result = extract_office_content(
                    office_type=office_type,
                    source_file_id=exfil_file_id,
                    file_id=thread_id,
                )
            except Exception as e:
                warnings.append(f"Drive exfil conversion failed, falling back to Gmail: {e}")
                exfil_file_id = None
                source_label = "gmail"

        if not exfil_file_id:
            content_bytes = _download_attachment_bytes(target_msg, target_att, mime_type)
            result = extract_office_content(
                office_type=office_type,
                file_bytes=content_bytes,
                file_id=thread_id,
            )
        output_format = "csv" if office_type == "xlsx" else "markdown"
        content_filename = f"content.{result.extension}"

        folder = get_deposit_folder(office_type, title, thread_id, base_path=base_path)
        content_path = write_content(folder, result.content, filename=content_filename)

        all_warnings = warnings + result.warnings
        extra: dict[str, Any] = {"source": source_label, "gmail_thread_id": thread_id}
        if all_warnings:
            extra["warnings"] = all_warnings
        write_manifest(folder, office_type, title, thread_id, extra=extra)

        return FetchResult(
            path=str(folder),
            content_file=str(content_path),
            format=output_format,
            type=office_type,
            metadata={
                "title": attachment_name,
                "source": source_label,
                "gmail_thread_id": thread_id,
            },
        )

    # PDF and images need bytes (no copy-convert shortcut)
    # Download from pre-exfil Drive or Gmail as appropriate
    if mime_type == "application/pdf" or mime_type.startswith("image/"):
        content_bytes = None
        if exfil_file_id:
            try:
                content_bytes = download_file(exfil_file_id)
            except Exception as e:
                warnings.append(f"Drive exfil download failed, falling back to Gmail: {e}")
                source_label = "gmail"

        if content_bytes is None:
            content_bytes = _download_attachment_bytes(target_msg, target_att, mime_type)
            source_label = "gmail"

    # PDF
    if mime_type == "application/pdf":
        pdf_result = extract_pdf_content(file_bytes=content_bytes, file_id=thread_id)

        folder = get_deposit_folder("pdf", title, thread_id, base_path=base_path)
        content_path = write_content(folder, pdf_result.content)

        all_warnings = warnings + pdf_result.warnings
        extra = {
            "source": source_label,
            "gmail_thread_id": thread_id,
            "extraction_method": pdf_result.method,
            "char_count": pdf_result.char_count,
        }
        if all_warnings:
            extra["warnings"] = all_warnings
        write_manifest(folder, "pdf", title, thread_id, extra=extra)

        return FetchResult(
            path=str(folder),
            content_file=str(content_path),
            format="markdown",
            type="pdf",
            metadata={
                "title": attachment_name,
                "source": source_label,
                "gmail_thread_id": thread_id,
                "extraction_method": pdf_result.method,
            },
        )

    # Images
    if mime_type.startswith("image/"):
        folder = get_deposit_folder("image", title, thread_id, base_path=base_path)
        image_path = write_image(folder, content_bytes, attachment_name)

        extra = {"source": source_label, "gmail_thread_id": thread_id}
        if warnings:
            extra["warnings"] = warnings
        write_manifest(folder, "image", title, thread_id, extra=extra)

        return FetchResult(
            path=str(folder),
            content_file=str(image_path),
            format="image",
            type="image",
            metadata={
                "title": attachment_name,
                "source": source_label,
                "gmail_thread_id": thread_id,
            },
        )

    # Unsupported type
    return FetchError(
        kind="extraction_failed",
        message=f"Cannot extract attachment with MIME type: {mime_type}",
    )


def fetch_drive(file_id: str, base_path: Path | None = None) -> FetchResult | FetchError:
    """Fetch Drive file, route by type, extract content, deposit to workspace."""
    # Get metadata to determine type
    metadata = get_file_metadata(file_id)
    mime_type = metadata.get("mimeType", "")
    title = metadata.get("name", "untitled")

    # Parse email context for cross-source linkage (exfil'd files)
    email_context = _parse_email_context(metadata.get("description"))

    # Route by MIME type
    if mime_type == GOOGLE_DOC_MIME:
        return fetch_doc(file_id, title, metadata, email_context, base_path=base_path)
    elif mime_type == GOOGLE_SHEET_MIME:
        return fetch_sheet(file_id, title, metadata, email_context, base_path=base_path)
    elif mime_type == GOOGLE_SLIDES_MIME:
        return fetch_slides(file_id, title, metadata, email_context, base_path=base_path)
    elif is_media_file(mime_type):
        return fetch_video(file_id, title, metadata, email_context, base_path=base_path)
    elif mime_type == "application/pdf":
        return fetch_pdf(file_id, title, metadata, email_context, base_path=base_path)
    elif (office_type := get_office_type_from_mime(mime_type)):
        return fetch_office(file_id, title, metadata, office_type, email_context, base_path=base_path)
    elif is_text_file(mime_type):
        return fetch_text(file_id, title, metadata, email_context, base_path=base_path)
    elif is_image_file(mime_type):
        return fetch_image_file(file_id, title, metadata, email_context, base_path=base_path)
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


def fetch_doc(doc_id: str, title: str, metadata: dict[str, Any], email_context: EmailContext | None = None, *, base_path: Path | None = None) -> FetchResult:
    """Fetch Google Doc with open comments included."""
    doc_data = fetch_document(doc_id)
    content = extract_doc_content(doc_data)

    folder = get_deposit_folder("doc", title, doc_id, base_path=base_path)
    content_path = write_content(folder, content)

    # Enrich with open comments (sous-chef philosophy)
    open_comment_count, _ = _enrich_with_comments(doc_id, folder)

    extra: dict[str, Any] = {"tab_count": len(doc_data.tabs) if doc_data.tabs else 1}
    if doc_data.warnings:
        extra["warnings"] = doc_data.warnings
    if open_comment_count > 0:
        extra["open_comment_count"] = open_comment_count
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


def fetch_sheet(sheet_id: str, title: str, metadata: dict[str, Any], email_context: EmailContext | None = None, *, base_path: Path | None = None) -> FetchResult:
    """Fetch Google Sheet with charts rendered as PNGs and open comments included."""
    sheet_data = fetch_spreadsheet(sheet_id)
    content = extract_sheets_content(sheet_data)

    folder = get_deposit_folder("sheet", title, sheet_id, base_path=base_path)
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

    # Enrich with open comments (sous-chef philosophy)
    open_comment_count, _ = _enrich_with_comments(sheet_id, folder)

    # Build manifest extras
    extra: dict[str, Any] = {"sheet_count": len(sheet_data.sheets)}
    if chart_count > 0:
        extra["chart_count"] = chart_count
        extra["chart_render_time_ms"] = sheet_data.chart_render_time_ms
    if sheet_data.warnings:
        extra["warnings"] = sheet_data.warnings
    if open_comment_count > 0:
        extra["open_comment_count"] = open_comment_count
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


def fetch_slides(presentation_id: str, title: str, metadata: dict[str, Any], email_context: EmailContext | None = None, *, base_path: Path | None = None) -> FetchResult:
    """Fetch Google Slides with open comments included."""
    # Enable thumbnails - selective logic in adapter skips stock photos/text-only
    presentation_data = fetch_presentation(presentation_id, include_thumbnails=True)
    content = extract_slides_content(presentation_data)

    folder = get_deposit_folder("slides", title, presentation_id, base_path=base_path)
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

    # Enrich with open comments (sous-chef philosophy)
    open_comment_count, _ = _enrich_with_comments(presentation_id, folder)

    extra: dict[str, Any] = {
        "slide_count": len(presentation_data.slides),
        "has_thumbnails": thumbnail_count > 0,
        "thumbnail_count": thumbnail_count,
    }
    if thumbnail_failures:
        extra["thumbnail_failures"] = thumbnail_failures
    if presentation_data.warnings:
        extra["warnings"] = presentation_data.warnings
    if open_comment_count > 0:
        extra["open_comment_count"] = open_comment_count
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


def fetch_video(file_id: str, title: str, metadata: dict[str, Any], email_context: EmailContext | None = None, *, base_path: Path | None = None) -> FetchResult:
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
    folder = get_deposit_folder("video", title, file_id, base_path=base_path)
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


def fetch_pdf(file_id: str, title: str, metadata: dict[str, Any], email_context: EmailContext | None = None, *, base_path: Path | None = None) -> FetchResult:
    """
    Fetch PDF file with hybrid extraction strategy.

    Uses adapters/pdf.py which tries markitdown first, falls back to Drive
    conversion for complex/image-heavy PDFs.
    """
    # Extract via adapter (handles download + hybrid extraction)
    result = fetch_and_extract_pdf(file_id)

    # Deposit to workspace
    folder = get_deposit_folder("pdf", title, file_id, base_path=base_path)
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


def fetch_office(file_id: str, title: str, metadata: dict[str, Any], office_type: OfficeType, email_context: EmailContext | None = None, *, base_path: Path | None = None) -> FetchResult:
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
    folder = get_deposit_folder(office_type, title, file_id, base_path=base_path)
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


def fetch_text(file_id: str, title: str, metadata: dict[str, Any], email_context: EmailContext | None = None, *, base_path: Path | None = None) -> FetchResult:
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
    folder = get_deposit_folder("text", title, file_id, base_path=base_path)
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


def fetch_image_file(file_id: str, title: str, metadata: dict[str, Any], email_context: EmailContext | None = None, *, base_path: Path | None = None) -> FetchResult:
    """
    Fetch image file (PNG, JPEG, GIF, WEBP, SVG, etc.).

    For raster images: deposit as-is.
    For SVG: deposit raw SVG + render to PNG (Claude can view PNGs but not SVGs).
    """
    mime_type = metadata.get("mimeType", "")

    # Fetch via adapter (handles download + SVG rendering)
    result = adapter_fetch_image(file_id, title, mime_type)

    # Deposit to workspace
    folder = get_deposit_folder("image", title, file_id, base_path=base_path)

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


def _fetch_web_pdf(url: str, web_data: WebData, base_path: Path | None = None) -> FetchResult:
    """
    Handle a web URL that returned application/pdf Content-Type.

    Two paths depending on response size:
    - Small PDFs: raw_bytes in memory → extract_pdf_content(file_bytes=...)
    - Large PDFs: temp_path on disk → extract_pdf_content(file_path=...)

    Caller (fetch_web) is responsible for temp_path cleanup via finally block.
    """
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]

    if web_data.temp_path:
        # Large PDF: extract directly from temp file (memory-safe)
        result = extract_pdf_content(file_id=url_hash, file_path=web_data.temp_path)
    elif web_data.raw_bytes:
        # Small PDF: extract from memory
        result = extract_pdf_content(file_bytes=web_data.raw_bytes, file_id=url_hash)
    else:
        raise MiseError(ErrorKind.EXTRACTION_FAILED, f"No PDF content received from {url}")

    # Use filename from URL or fallback
    url_path = urlparse(url).path
    filename = unquote(url_path.rsplit('/', 1)[-1])
    title = filename.removesuffix('.pdf').strip() or "web-pdf"

    # Deposit to workspace
    folder = get_deposit_folder("pdf", title, url_hash, base_path=base_path)
    content_path = write_content(folder, result.content)

    extra: dict[str, Any] = {
        "url": url,
        "char_count": result.char_count,
        "extraction_method": result.method,
    }
    if result.warnings:
        extra["warnings"] = result.warnings
    write_manifest(folder, "pdf", title, url_hash, extra=extra)

    result_meta: dict[str, Any] = {
        "title": title,
        "url": url,
        "extraction_method": result.method,
        "char_count": result.char_count,
    }
    if result.warnings:
        result_meta["warnings"] = result.warnings

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="pdf",
        metadata=result_meta,
    )


def _fetch_web_office(url: str, web_data: WebData, office_type: OfficeType, base_path: Path | None = None) -> FetchResult:
    """
    Handle a web URL that returned an Office Content-Type.

    Two paths depending on response size:
    - Small files: raw_bytes in memory → extract_office_content(file_bytes=...)
    - Large files: temp_path on disk → extract_office_content(file_path=...)

    Caller (fetch_web) is responsible for temp_path cleanup via finally block.
    """
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]

    if web_data.temp_path:
        # Large file: convert directly from disk (memory-safe)
        result = extract_office_content(
            office_type,
            file_path=web_data.temp_path,
            file_id=url_hash,
        )
        result.warnings.insert(0, "Large file: extracted from temp file")
    elif web_data.raw_bytes:
        # Small file: convert from memory
        result = extract_office_content(
            office_type,
            file_bytes=web_data.raw_bytes,
            file_id=url_hash,
        )
    else:
        raise MiseError(
            ErrorKind.EXTRACTION_FAILED,
            f"No Office content received from {url}",
        )

    # Use filename from URL or fallback
    url_path = urlparse(url).path
    filename = unquote(url_path.rsplit('/', 1)[-1])
    title = filename.rsplit('.', 1)[0].strip() or f"web-{office_type}"

    # Determine output format
    output_format = "csv" if office_type == "xlsx" else "markdown"
    content_filename = f"content.{result.extension}"

    # Deposit to workspace
    folder = get_deposit_folder(office_type, title, url_hash, base_path=base_path)
    content_path = write_content(folder, result.content, filename=content_filename)

    extra: dict[str, Any] = {
        "url": url,
    }
    if result.warnings:
        extra["warnings"] = result.warnings
    write_manifest(folder, office_type, title, url_hash, extra=extra)

    result_meta: dict[str, Any] = {
        "title": title,
        "url": url,
        "office_type": office_type,
    }
    if result.warnings:
        result_meta["warnings"] = result.warnings

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format=output_format,
        type=office_type,
        metadata=result_meta,
    )


def fetch_web(url: str, base_path: Path | None = None) -> FetchResult:
    """
    Fetch web page, extract content, deposit to workspace.

    Uses tiered extraction strategy:
    1. HTTP fetch (fast path)
    2. Browser rendering fallback for JS-rendered content
    3. trafilatura for content extraction

    Args:
        url: Web URL to fetch

    Returns:
        FetchResult with path to deposited content
    """
    # Fetch via adapter (probes URL, captures Content-Type)
    web_data = fetch_web_content(url)

    ct = web_data.content_type.lower()

    # Route binary content to appropriate extractors instead of HTML path
    # PDF
    if 'application/pdf' in ct:
        try:
            return _fetch_web_pdf(url, web_data, base_path=base_path)
        finally:
            if web_data.temp_path:
                web_data.temp_path.unlink(missing_ok=True)

    # Office (DOCX, XLSX, PPTX)
    ct_bare = ct.split(';')[0].strip()
    office_type = get_office_type_from_mime(ct_bare)
    if office_type:
        try:
            return _fetch_web_office(url, web_data, office_type, base_path=base_path)
        finally:
            if web_data.temp_path:
                web_data.temp_path.unlink(missing_ok=True)

    # Extract content via extractor (pure function)
    content = extract_web_content(web_data)

    # Extract title for folder naming
    title = extract_title(web_data.html) or "web-page"

    # Generate stable ID from URL for deduplication
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]

    # Deposit to workspace
    folder = get_deposit_folder(
        content_type="web",
        title=title,
        resource_id=url_hash,
        base_path=base_path,
    )
    content_path = write_content(folder, content)

    # Build manifest extras
    extra: dict[str, Any] = {
        "url": url,
        "final_url": web_data.final_url,
        "render_method": web_data.render_method,
        "word_count": len(content.split()),
    }
    if web_data.warnings:
        extra["warnings"] = web_data.warnings

    write_manifest(folder, "web", title, url_hash, extra=extra)

    # Build result metadata
    result_meta: dict[str, Any] = {
        "title": title,
        "url": url,
        "final_url": web_data.final_url,
        "render_method": web_data.render_method,
        "word_count": len(content.split()),
    }
    if web_data.warnings:
        result_meta["warnings"] = web_data.warnings

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="web",
        metadata=result_meta,
    )


def do_fetch(file_id: str, base_path: Path | None = None, attachment: str | None = None) -> FetchResult | FetchError:
    """
    Main fetch entry point.

    Detects ID type, routes to appropriate fetcher, handles errors.

    Args:
        file_id: Drive file ID, Gmail thread ID, or URL
        base_path: Base directory for deposits (defaults to cwd)
        attachment: Specific attachment filename to extract from Gmail thread
    """
    try:
        # Detect ID type and normalize
        source, normalized_id = detect_id_type(file_id)

        # Single-attachment fetch (Gmail only)
        if attachment:
            if source != "gmail":
                return FetchError(
                    kind="invalid_input",
                    message="attachment parameter only works with Gmail thread/message IDs",
                )
            return fetch_attachment(normalized_id, attachment, base_path=base_path)

        # Route to appropriate fetcher
        if source == "gmail":
            return fetch_gmail(normalized_id, base_path=base_path)
        elif source == "web":
            return fetch_web(normalized_id, base_path=base_path)
        else:
            return fetch_drive(normalized_id, base_path=base_path)

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
