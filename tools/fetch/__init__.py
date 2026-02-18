"""
Fetch tool package — routes by ID type, extracts content, deposits to workspace.

Re-exports all public symbols so `from tools.fetch import X` continues to work.
"""

# Router (entry points)
from .router import do_fetch, detect_id_type

# Common helpers
from .common import (
    _build_cues, _build_email_context_metadata, _enrich_with_comments,
    _deposit_pdf_thumbnails, is_text_file, TEXT_MIME_TYPES,
)

# Drive fetchers
from .drive import (
    fetch_drive, fetch_doc, fetch_sheet, fetch_slides, fetch_video,
    fetch_pdf, fetch_office, fetch_text, fetch_image_file,
)

# Gmail fetchers
from .gmail import (
    fetch_gmail, fetch_attachment, _is_extractable_attachment,
    _match_exfil_file, _deposit_attachment_content, _extract_from_drive,
    _extract_attachment_content, _download_attachment_bytes,
    OFFICE_MIME_TYPES, MAX_EAGER_ATTACHMENTS,
)

# Web fetchers
from .web import fetch_web, _fetch_web_pdf, _fetch_web_office
