"""
Web content fetch — HTTP pages, web-hosted PDFs and Office files.
"""

import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from adapters.office import convert_office_content, get_office_type_from_mime, OfficeType
from adapters.pdf import convert_pdf_content, render_pdf_pages
from adapters.web import fetch_web_content
from extractors.web import extract_web_content, extract_title, EXTRACTION_FAILED_CUE
from models import MiseError, ErrorKind, FetchResult, WebData
from workspace import get_deposit_folder, write_content, write_manifest

from .common import _build_cues, _deposit_pdf_thumbnails


def _fetch_web_pdf(url: str, web_data: WebData, base_path: Path | None = None) -> FetchResult:
    """
    Handle a web URL that returned application/pdf Content-Type.

    Two paths depending on response size:
    - Small PDFs: raw_bytes in memory → convert_pdf_content(file_bytes=...)
    - Large PDFs: temp_path on disk → convert_pdf_content(file_path=...)

    Caller (fetch_web) is responsible for temp_path cleanup via finally block.
    """
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]

    if web_data.temp_path:
        # Large PDF: check magic bytes at start of file
        with open(web_data.temp_path, "rb") as f:
            magic = f.read(5)
        if magic != b"%PDF-":
            raise MiseError(
                ErrorKind.EXTRACTION_FAILED,
                f"Content-Type says application/pdf but content is not PDF (starts with {magic[:20]!r}). "
                f"The server at {urlparse(url).netloc} may be returning an error page.",
            )
        result = convert_pdf_content(file_id=url_hash, file_path=web_data.temp_path)
    elif web_data.raw_bytes:
        # Small PDF: check magic bytes before extraction
        if not web_data.raw_bytes.startswith(b"%PDF-"):
            raise MiseError(
                ErrorKind.EXTRACTION_FAILED,
                f"Content-Type says application/pdf but content is not PDF (starts with {web_data.raw_bytes[:20]!r}). "
                f"The server at {urlparse(url).netloc} may be returning an error page.",
            )
        result = convert_pdf_content(file_bytes=web_data.raw_bytes, file_id=url_hash)
    else:
        raise MiseError(ErrorKind.EXTRACTION_FAILED, f"No PDF content received from {url}")

    # Render thumbnails — uses same bytes/path as text extraction
    try:
        if web_data.temp_path:
            result.thumbnails = render_pdf_pages(file_path=web_data.temp_path)
        elif web_data.raw_bytes:
            result.thumbnails = render_pdf_pages(file_bytes=web_data.raw_bytes)
    except Exception as e:
        result.warnings.append(f"Thumbnail rendering failed: {e}")

    # Use filename from URL or fallback
    url_path = urlparse(url).path
    filename = unquote(url_path.rsplit('/', 1)[-1])
    title = filename.removesuffix('.pdf').strip() or "web-pdf"

    # Deposit to workspace
    folder = get_deposit_folder("pdf", title, url_hash, base_path=base_path)
    content_path = write_content(folder, result.content)

    # Deposit page thumbnails (shared helper writes PNGs, returns manifest extras)
    thumb_extras = _deposit_pdf_thumbnails(folder, result)

    extra: dict[str, Any] = {
        "url": url,
        "char_count": result.char_count,
        "extraction_method": result.method,
        **thumb_extras,
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

    cues = _build_cues(folder, warnings=result.warnings)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="pdf",
        metadata=result_meta,
        cues=cues,
    )


def _fetch_web_office(url: str, web_data: WebData, office_type: OfficeType, base_path: Path | None = None) -> FetchResult:
    """
    Handle a web URL that returned an Office Content-Type.

    Two paths depending on response size:
    - Small files: raw_bytes in memory → convert_office_content(file_bytes=...)
    - Large files: temp_path on disk → convert_office_content(file_path=...)

    Caller (fetch_web) is responsible for temp_path cleanup via finally block.
    """
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]

    if web_data.temp_path:
        # Large file: convert directly from disk (memory-safe)
        result = convert_office_content(
            office_type,
            file_path=web_data.temp_path,
            file_id=url_hash,
        )
        result.warnings.insert(0, "Large file: extracted from temp file")
    elif web_data.raw_bytes:
        # Small file: convert from memory
        result = convert_office_content(
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

    cues = _build_cues(folder, warnings=result.warnings)

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format=output_format,
        type=office_type,
        metadata=result_meta,
        cues=cues,
    )


def fetch_web(url: str, base_path: Path | None = None) -> FetchResult:
    """
    Fetch web page, extract content, deposit to workspace.

    Uses tiered extraction strategy:
    1. HTTP fetch (fast path)
    2. Browser rendering fallback for JS-rendered content
    3. trafilatura for content extraction
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

    # Use pre-extracted content from passe if available, else run trafilatura
    if web_data.pre_extracted_content:
        content = web_data.pre_extracted_content
    else:
        content = extract_web_content(web_data)

    # Extract title for folder naming
    # Priority: HTML <title> → first H1 in pre-extracted content → URL-derived → fallback
    title = extract_title(web_data.html)
    if not title and web_data.pre_extracted_content:
        h1_match = re.search(r'^#\s+(.+)', web_data.pre_extracted_content.lstrip(), re.MULTILINE)
        if h1_match:
            # Strip markdown link syntax: [text](url) → text
            title = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', h1_match.group(1)).strip()
    if not title:
        # Try URL path as last resort before generic fallback
        url_path = urlparse(url).path.rstrip('/')
        if url_path and url_path != '/':
            title = unquote(url_path.rsplit('/', 1)[-1]).replace('-', ' ').replace('_', ' ')
    if not title:
        title = "web-page"

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

    cues = _build_cues(folder, warnings=web_data.warnings)

    # Signal extraction failure so callers can react (e.g. retry with browser)
    if EXTRACTION_FAILED_CUE in content:
        cues['extraction_failed'] = True

    return FetchResult(
        path=str(folder),
        content_file=str(content_path),
        format="markdown",
        type="web",
        metadata=result_meta,
        cues=cues,
    )
