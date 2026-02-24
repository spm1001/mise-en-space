"""
Gmail fetch — thread extraction, attachment handling, pre-exfil routing.
"""

from pathlib import Path
from typing import Any

from adapters.drive import download_file, lookup_exfiltrated
from adapters.gmail import fetch_thread, download_attachment
from adapters.office import convert_office_content, get_office_type_from_mime
from adapters.pdf import convert_pdf_content, render_pdf_pages
from extractors.gmail import extract_thread_content
from extractors.image import resize_image_bytes
from models import MiseError, FetchResult, FetchError, EmailAttachment
from workspace import get_deposit_folder, write_content, write_manifest, write_image

from .common import _build_cues, _deposit_pdf_thumbnails


# MIME types for Office files that are too slow to extract eagerly (5-10s each)
OFFICE_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # pptx
    "application/msword",  # doc
    "application/vnd.ms-excel",  # xls
    "application/vnd.ms-powerpoint",  # ppt
}

# NOTE: In conversations with >20 accumulated images the limit drops to 2000px.
# We can't know conversation context at deposit time — note dimension in metadata
# so Claude can judge risk in long conversations.
SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})

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

    # Extract supported images only — the pre-download size/format check in
    # fetch_gmail handles the finer filtering; this gates the drive-exfil path too.
    if mime_type in SUPPORTED_IMAGE_MIME_TYPES:
        return True

    return False


def _names_match(att_filename: str, drive_filename: str) -> bool:
    """
    Exact or stem match between an attachment filename and a Drive filename.

    The exfil script may modify filenames:
    - ensureExtension: "report" → "report.pdf"  (stem match catches this)
    - smartFilename: UUID names get date+sender prefix  (no match — falls to fallback)
    """
    if att_filename == drive_filename:
        return True
    att_stem = att_filename.rsplit(".", 1)[0] if "." in att_filename else att_filename
    drive_stem = drive_filename.rsplit(".", 1)[0] if "." in drive_filename else drive_filename
    return att_stem == drive_stem


def _match_exfil_for_message(
    attachments: list[EmailAttachment],
    exfil_files: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """
    Match all attachments for a message to pre-exfil'd Drive files.

    Uses a consumed pool so each exfil file is assigned at most once.
    This prevents the same Drive file matching multiple attachments — the
    original bug that deposited DOCX bytes under PNG filenames.

    Two-pass algorithm:
    1. Name/stem matches consume from pool greedily (deterministic, unambiguous).
    2. 1:1 fallback: if exactly one attachment is still unmatched AND exactly one
       exfil file remains AND their MIME categories agree → assign it.
       This handles UUID-renamed files where name matching fails entirely.

    Returns dict mapping attachment_id → exfil file dict.
    """
    if not exfil_files or not attachments:
        return {}

    pool = list(exfil_files)  # mutable copy — consumed as matches are made
    matched: dict[str, dict[str, Any]] = {}

    # Pass 1: exact and stem matches, consuming from pool
    for att in attachments:
        for i, f in enumerate(pool):
            if _names_match(att.filename, f["name"]):
                matched[att.attachment_id] = f
                pool.pop(i)
                break

    # Pass 2: 1:1 fallback — only when certainty is absolute
    unmatched = [a for a in attachments if a.attachment_id not in matched]
    if len(unmatched) == 1 and len(pool) == 1:
        att, exfil = unmatched[0], pool[0]
        exfil_cat = exfil.get("mimeType", "").split("/")[0]
        att_cat = att.mime_type.split("/")[0]
        if exfil_cat and exfil_cat == att_cat:
            matched[att.attachment_id] = exfil

    return matched


def _deposit_attachment_content(
    content_bytes: bytes,
    filename: str,
    mime_type: str,
    file_id: str,
    folder: Path,
) -> dict[str, Any] | None:
    """
    Route attachment bytes by MIME type and deposit to folder.

    Shared by both Drive (pre-exfil) and Gmail download paths.
    Returns extraction result dict or None if type not handled.
    """
    if mime_type == "application/pdf":
        # No thumbnails here — this deposits into the shared thread folder.
        # Multiple PDF attachments would collide on page_01.png filenames.
        # The raw PDF is deposited alongside for Claude to view directly.
        # Single-attachment fetch (fetch_attachment) gets its own folder and does render thumbnails.
        result = convert_pdf_content(content_bytes, file_id=file_id)

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
        # Open with PIL, resize if needed, deposit.
        # Oversized images (long edge > MAX_LONG_EDGE_PX) are scaled down rather
        # than skipped — the API downscales internally above 1568px anyway.
        # Only PIL failures (genuine MIME mismatch, e.g. DOCX renamed .png) cause
        # a skip — depositing non-image bytes as image/png causes a hard 400 that
        # poisons the session and cannot be fixed by resizing.
        try:
            resized = resize_image_bytes(content_bytes, mime_type)
        except ValueError as e:
            return {
                "filename": filename,
                "mime_type": mime_type,
                "skipped": True,
                "reason": str(e),
            }

        deposited_filename = filename
        if resized.jpeg_fallback:
            # PNG was still > 4.5MB after resize — converted to JPEG.
            deposited_filename = filename.rsplit(".", 1)[0] + ".jpg"

        write_image(folder, resized.content_bytes, deposited_filename)

        result: dict[str, Any] = {
            "filename": filename,
            "mime_type": resized.mime_type,
            "extracted": True,
            "deposited_as": deposited_filename,
            "dimensions": resized.dimensions,
        }
        if resized.original_dimensions:
            result["original_dimensions"] = resized.original_dimensions
            result["scaled_to"] = resized.dimensions
            result["scale_factor"] = resized.scale_factor
        if resized.jpeg_fallback:
            result["jpeg_fallback"] = True
        return result

    return None


def _extract_from_drive(
    file_id: str,
    filename: str,
    mime_type: str,
    folder: Path,
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
    att: EmailAttachment,
    folder: Path,
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
    skipped_images: list[dict[str, Any]] = []
    extracted_attachments: list[dict[str, Any]] = []
    extraction_warnings: list[str] = []
    extracted_count = 0

    # Pre-exfil lookup: check if attachments already exist in Drive
    # (indexed by fullText, faster than Gmail download + extraction)
    message_ids = [msg.message_id for msg in thread_data.messages]
    exfiltrated = lookup_exfiltrated(message_ids)

    for msg in thread_data.messages:
        # Match ALL attachments for this message to exfil'd Drive files at once.
        # Consumed-pool approach prevents one Drive file matching multiple attachments.
        exfil_files = exfiltrated.get(msg.message_id, [])
        exfil_matches = _match_exfil_for_message(msg.attachments, exfil_files)

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

            # Pre-download image format check (size is no longer a skip criterion —
            # oversized images are resized post-download rather than skipped).
            if att.mime_type.startswith("image/"):
                if att.mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
                    skipped_images.append({
                        "filename": att.filename,
                        "mime_type": att.mime_type,
                        "reason": "unsupported format (API supports: jpeg, png, gif, webp)",
                    })
                    continue

            # Limit eager extraction
            if extracted_count >= MAX_EAGER_ATTACHMENTS:
                extraction_warnings.append(
                    f"Attachment limit ({MAX_EAGER_ATTACHMENTS}) reached, "
                    f"skipping: {att.filename}"
                )
                continue

            # Try pre-exfil'd Drive copy first (faster, already indexed)
            exfil_match = exfil_matches.get(att.attachment_id)
            if exfil_match and _is_extractable_attachment(att.mime_type):
                result = _extract_from_drive(
                    file_id=exfil_match["file_id"],
                    filename=att.filename,
                    mime_type=att.mime_type,
                    folder=folder,
                    warnings=extraction_warnings,
                )
                if result and result.get("skipped"):
                    skipped_images.append(result)
                    continue
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
                if result and result.get("skipped"):
                    skipped_images.append(result)
                elif result:
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
    if skipped_images:
        extra["skipped_images"] = skipped_images

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
    if skipped_images:
        metadata["skipped_images"] = skipped_images
        img_examples = [
            "fetch('{}', attachment='{}')".format(thread_id, img["filename"])
            for img in skipped_images
        ]
        metadata["skipped_images_hint"] = (
            f"Images skipped (too large or unsupported format for Claude API). "
            f"To fetch individually: {'; '.join(img_examples)}"
        )

    # Build participants list (unique, ordered by first appearance)
    seen_participants: set[str] = set()
    participants: list[str] = []
    for msg in thread_data.messages:
        if msg.from_address and msg.from_address not in seen_participants:
            seen_participants.add(msg.from_address)
            participants.append(msg.from_address)

    # Date range
    all_warnings = (thread_data.warnings or []) + extraction_warnings
    dates = [msg.date for msg in thread_data.messages if hasattr(msg, "date") and msg.date]
    date_range = None
    if dates:
        first = min(dates)
        last = max(dates)
        if first == last:
            date_range = first.strftime("%Y-%m-%d") if hasattr(first, "strftime") else str(first)[:10]
        else:
            date_range = f"{first.strftime('%Y-%m-%d') if hasattr(first, 'strftime') else str(first)[:10]} to {last.strftime('%Y-%m-%d') if hasattr(last, 'strftime') else str(last)[:10]}"

    cues = _build_cues(
        folder,
        warnings=all_warnings if all_warnings else None,
        participants=participants,
        has_attachments=thread_data.has_attachments,
        date_range=date_range,
    )

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="gmail",
        metadata=metadata,
        cues=cues,
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
            if att.filename.lower() == attachment_name.lower():
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
        exfil_matches = _match_exfil_for_message([target_att], exfil_files)
        exfil_match = exfil_matches.get(target_att.attachment_id)
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
                result = convert_office_content(
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
            result = convert_office_content(
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

        cues = _build_cues(folder, warnings=all_warnings if all_warnings else None)

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
            cues=cues,
        )

    # PDF and images need bytes — no copy-convert shortcut because:
    # PDF: markitdown runs locally first (needs bytes), Drive is fallback only
    # Images: deposited as files for Claude to view (need bytes on disk)
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
        pdf_result = convert_pdf_content(file_bytes=content_bytes, file_id=thread_id)

        # Render thumbnails (own folder, no collision risk)
        try:
            pdf_result.thumbnails = render_pdf_pages(file_bytes=content_bytes)
        except Exception as e:
            pdf_result.warnings.append(f"Thumbnail rendering failed: {e}")

        folder = get_deposit_folder("pdf", title, thread_id, base_path=base_path)
        content_path = write_content(folder, pdf_result.content)

        # Deposit thumbnails via shared helper
        thumb_extras = _deposit_pdf_thumbnails(folder, pdf_result)

        all_warnings = warnings + pdf_result.warnings
        extra = {
            "source": source_label,
            "gmail_thread_id": thread_id,
            "extraction_method": pdf_result.method,
            "char_count": pdf_result.char_count,
            **thumb_extras,
        }
        if all_warnings:
            extra["warnings"] = all_warnings
        write_manifest(folder, "pdf", title, thread_id, extra=extra)

        cues = _build_cues(folder, warnings=all_warnings if all_warnings else None)

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
            cues=cues,
        )

    # Images
    if mime_type.startswith("image/"):
        try:
            resized = resize_image_bytes(content_bytes, mime_type)
        except ValueError as e:
            return FetchError(
                kind="extraction_failed",
                message=f"Image validation failed: {e}",
            )

        deposited_filename = attachment_name
        if resized.jpeg_fallback:
            deposited_filename = attachment_name.rsplit(".", 1)[0] + ".jpg"

        folder = get_deposit_folder("image", title, thread_id, base_path=base_path)
        image_path = write_image(folder, resized.content_bytes, deposited_filename)

        image_meta: dict[str, Any] = {
            "title": attachment_name,
            "source": source_label,
            "gmail_thread_id": thread_id,
            "mime_type": resized.mime_type,
            "dimensions": resized.dimensions,
        }
        if resized.original_dimensions:
            image_meta["original_dimensions"] = resized.original_dimensions
            image_meta["scaled_to"] = resized.dimensions
            image_meta["scale_factor"] = resized.scale_factor
        if resized.jpeg_fallback:
            image_meta["jpeg_fallback"] = True

        extra: dict[str, Any] = {"source": source_label, "gmail_thread_id": thread_id, **image_meta}
        if warnings:
            extra["warnings"] = warnings
        write_manifest(folder, "image", title, thread_id, extra=extra)

        cues = _build_cues(folder, warnings=warnings if warnings else None)

        return FetchResult(
            path=str(folder),
            content_file=str(image_path),
            format="image",
            type="image",
            metadata=image_meta,
            cues=cues,
        )

    # Unsupported type
    return FetchError(
        kind="extraction_failed",
        message=f"Cannot extract attachment with MIME type: {mime_type}",
    )
