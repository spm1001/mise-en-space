"""
Gmail fetch — thread extraction, attachment handling, pre-exfil routing.
"""

from pathlib import Path
from typing import Any

from adapters.drive import download_file, lookup_exfiltrated
from adapters.calendar import get_event_by_ical_uid
from adapters.gmail import fetch_thread
from adapters.office import convert_office_content, get_office_type_from_mime
from adapters.pdf import convert_pdf_content, render_pdf_pages
from extractors.gmail import extract_thread_content, parse_ics_uid
from extractors.image import resize_image_bytes
from models import FetchResult, FetchError, InviteState
from workspace import get_deposit_folder, write_content, write_manifest, write_image

from .common import _build_cues, _deposit_pdf_thumbnails
from .gmail_attachments import (
    MAX_EAGER_ATTACHMENTS,
    _download_attachment_bytes,
    _extract_attachment_content,
    _extract_from_drive,
    _resolve_attachment_mime,
    classify_attachment,
)
from .gmail_exfil import _match_exfil_for_message
from .gmail_participants import _extract_participants


def _enrich_invite_state(messages: list[Any], warnings: list[str]) -> InviteState | None:
    """Resolve a thread's calendar invite to its LIVE event state (mise-pinodi).

    An invitation email is a frozen snapshot (its ICS says CONFIRMED forever);
    this reads the current Calendar state by iCalUID so a cancelled or
    rescheduled meeting is disclosed instead of repeated stale. When the live
    state is `cancelled`, a warning is appended so the stale body is flagged.

    Best-effort by design: a guest token may carry no calendar scope, so ANY
    failure (no ICS, no UID, no scope, transient error) returns None and never
    fails the fetch. Costs at most two API calls (attachment fetch + events
    lookup) and only when the thread actually carries an invite.
    """
    ics: tuple[Any, Any] | None = None
    for msg in messages:
        # calendar_attachments holds the ICS parts that were trivial-filtered
        # out of the user-facing `attachments` list (mise-pinodi).
        if msg.calendar_attachments:
            ics = (msg, msg.calendar_attachments[0])
            break
    if ics is None:
        return None

    msg, att = ics
    try:
        raw = _download_attachment_bytes(msg, att, att.mime_type or "text/calendar")
        uid = parse_ics_uid(raw.decode("utf-8", "replace"))
        if not uid:
            return None
        state = get_event_by_ical_uid(uid)
        if state and state.status == "cancelled":
            when = f" (on {state.cancelled_at})" if state.cancelled_at else ""
            warnings.append(
                f"Calendar invite is a STALE SNAPSHOT: this meeting was "
                f"CANCELLED{when}. The email body still reads as a live invitation."
            )
        return state
    except Exception:
        # No calendar scope (guest mode), attachment gone, or transient error —
        # enrichment is a bonus, never a failure mode for the fetch.
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

            # Resolve Outlook-style octet-stream mis-tagging by filename
            # extension; dispatch on the resolved MIME, keep att.mime_type
            # as declared (see mise-dazode — fetch_attachment already does this).
            resolved_mime = _resolve_attachment_mime(att.mime_type, att.filename)
            if resolved_mime != att.mime_type:
                extraction_warnings.append(
                    f"Attachment '{att.filename}' declared "
                    f"application/octet-stream; treating as '{resolved_mime}' "
                    f"via filename extension"
                )

            category = classify_attachment(resolved_mime)

            # Skip Office files (note for manifest)
            if category == "office":
                skipped_office.append(att.filename)
                continue

            # Pre-download image format check (size is no longer a skip criterion —
            # oversized images are resized post-download rather than skipped).
            if category == "image_unsupported":
                skipped_images.append({
                    "filename": att.filename,
                    "mime_type": resolved_mime,
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

            eager = category in ("pdf", "image")

            # Try pre-exfil'd Drive copy first (faster, already indexed)
            exfil_match = exfil_matches.get(att.attachment_id)
            if exfil_match and eager:
                result = _extract_from_drive(
                    file_id=exfil_match["file_id"],
                    filename=att.filename,
                    mime_type=resolved_mime,
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
            if eager:
                result = _extract_attachment_content(
                    message_id=msg.message_id,
                    att=att,
                    folder=folder,
                    warnings=extraction_warnings,
                    mime_type=resolved_mime,
                )
                if result and result.get("skipped"):
                    skipped_images.append(result)
                elif result:
                    result["source"] = "gmail"
                    extracted_attachments.append(result)
                    extracted_count += 1

        all_drive_links.extend(msg.drive_links)

    # Invite-state enrichment: disclose live Calendar state for invitation
    # emails (cancelled/rescheduled). Appends a cancelled warning into
    # extraction_warnings so it flows to both the manifest and the cues.
    invite_state = _enrich_invite_state(thread_data.messages, extraction_warnings)

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

    # Label summary from message label_ids
    unread_count = sum(1 for msg in thread_data.messages if "UNREAD" in msg.label_ids)
    if unread_count:
        metadata["unread_count"] = unread_count
    # Collect unique labels across all messages
    all_label_ids: set[str] = set()
    for msg in thread_data.messages:
        all_label_ids.update(msg.label_ids)
    if all_label_ids:
        metadata["labels"] = sorted(all_label_ids)

    participants = _extract_participants(thread_data)

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

    # Add label-derived cues
    if unread_count:
        cues["unread_messages"] = unread_count
    notable_labels = all_label_ids & {"STARRED", "IMPORTANT"}
    if notable_labels:
        cues["notable_labels"] = sorted(notable_labels)

    # Live Calendar state for an invite thread (mise-pinodi)
    if invite_state is not None:
        cues["invite_state"] = invite_state.to_dict()

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="gmail",
        metadata=metadata,
        cues=cues,
    )


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

    # Resolve declared MIME via filename extension when sender mislabels as
    # application/octet-stream (Outlook/Exchange ships CSV/JSON/XML this way).
    mime_type = _resolve_attachment_mime(target_att.mime_type, target_att.filename)
    category = classify_attachment(mime_type)
    content_bytes: bytes | None
    warnings: list[str] = []
    if mime_type != target_att.mime_type:
        warnings.append(
            f"Declared MIME 'application/octet-stream' resolved to "
            f"'{mime_type}' via filename extension"
        )

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
    # (image_unsupported included: the deposit attempt produces the precise
    # "Image validation failed" error rather than a generic cannot-extract)
    if category in ("pdf", "image", "image_unsupported"):
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
    if category == "pdf":
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

    # Images (image_unsupported deliberately enters here: resize_image_bytes
    # raises ValueError with the precise unsupported-format message)
    if category in ("image", "image_unsupported"):
        assert content_bytes is not None  # filled by the bytes-fetch block above
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

        extra = {"source": source_label, "gmail_thread_id": thread_id, **image_meta}
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

    # Text formats — CSV, JSON, XML, plain text, etc. Deposit bytes as-is;
    # no extraction needed (Claude reads the file directly). Also handles
    # Outlook's octet-stream-tagged text attachments via the MIME resolver
    # at the top of this function.
    if category == "text":
        content_bytes = None
        if exfil_file_id:
            try:
                content_bytes = download_file(exfil_file_id)
            except Exception as e:
                warnings.append(f"Drive exfil download failed, falling back to Gmail: {e}")
                source_label = "gmail"
        if content_bytes is None:
            content_bytes = _download_attachment_bytes(target_msg, target_att, mime_type)

        content = content_bytes.decode("utf-8", errors="replace")
        ext = attachment_name.rsplit(".", 1)[-1].lower() if "." in attachment_name else "txt"
        content_filename = f"content.{ext}"

        folder = get_deposit_folder("text", title, thread_id, base_path=base_path)
        content_path = write_content(folder, content, filename=content_filename)

        extra = {
            "source": source_label,
            "gmail_thread_id": thread_id,
            "mime_type": mime_type,
            "char_count": len(content),
        }
        if warnings:
            extra["warnings"] = warnings
        write_manifest(folder, "text", title, thread_id, extra=extra)

        cues = _build_cues(folder, warnings=warnings if warnings else None)

        return FetchResult(
            path=str(folder),
            content_file=str(content_path),
            format="text",
            type="text",
            metadata={
                "title": attachment_name,
                "source": source_label,
                "gmail_thread_id": thread_id,
                "mime_type": mime_type,
                "char_count": len(content),
            },
            cues=cues,
        )

    # Unsupported type
    return FetchError(
        kind="extraction_failed",
        message=f"Cannot extract attachment with MIME type: {mime_type}",
    )
