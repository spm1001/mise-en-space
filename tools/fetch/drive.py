"""
Drive file fetch — routes by MIME type, extracts content, deposits to workspace.
"""

from pathlib import Path
from typing import Any

from adapters.drive import get_file_metadata, _parse_email_context, download_file, GOOGLE_DOC_MIME, GOOGLE_SHEET_MIME, GOOGLE_SLIDES_MIME
from adapters.docs import fetch_document
from adapters.sheets import fetch_spreadsheet
from adapters.slides import fetch_presentation
from adapters.genai import get_video_summary, is_media_file
from adapters.cdp import is_cdp_available
from adapters.pdf import fetch_and_extract_pdf, extract_pdf_content
from adapters.office import fetch_and_extract_office, get_office_type_from_mime, OfficeType
from adapters.image import fetch_image as adapter_fetch_image, is_image_file, is_svg
from extractors.docs import extract_doc_content
from extractors.sheets import extract_sheets_content
from extractors.slides import extract_slides_content
from models import FetchResult, FetchError, EmailContext
from workspace import get_deposit_folder, write_content, write_manifest, write_thumbnail, write_image, write_chart, write_charts_metadata

from .common import (
    _build_cues, _build_email_context_metadata, _enrich_with_comments,
    is_text_file,
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

    cues = _build_cues(
        folder,
        open_comment_count=open_comment_count,
        warnings=doc_data.warnings,
        email_context=email_context,
    )

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="doc",
        metadata=result_metadata,
        cues=cues,
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

    cues = _build_cues(
        folder,
        open_comment_count=open_comment_count,
        warnings=sheet_data.warnings,
        email_context=email_context,
    )

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="csv",
        type="sheet",
        metadata=result_meta,
        cues=cues,
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

    cues = _build_cues(
        folder,
        open_comment_count=open_comment_count,
        warnings=presentation_data.warnings,
        email_context=email_context,
    )

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="slides",
        metadata=result_meta,
        cues=cues,
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

    cues = _build_cues(folder, email_context=email_context)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="video",
        metadata=result_meta,
        cues=cues,
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

    cues = _build_cues(
        folder,
        warnings=result.warnings,
        email_context=email_context,
    )

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="pdf",
        metadata=result_meta,
        cues=cues,
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

    cues = _build_cues(
        folder,
        warnings=result.warnings,
        email_context=email_context,
    )

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format=output_format,
        type=office_type,
        metadata=result_meta,
        cues=cues,
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

    cues = _build_cues(folder, email_context=email_context)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format=output_format,
        type="text",
        metadata=result_meta,
        cues=cues,
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

    cues = _build_cues(
        folder,
        warnings=result.warnings,
        email_context=email_context,
    )

    return FetchResult(
        path=str(folder),
        content_file=content_file,
        format="image",
        type="image",
        metadata=result_meta,
        cues=cues,
    )
