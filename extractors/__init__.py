"""
Extractors — Pure functions for content extraction.

No MCP awareness, no Google API calls. Just transform input → output.
Easily testable with fixtures.
"""

from .sheets import extract_sheets_content
from .docs import extract_doc_content
from .gmail import (
    extract_thread_content,
    extract_message_content,
    parse_message_payload,
    parse_attachments_from_payload,
)
from .slides import (
    extract_slides_content,
    parse_presentation,
)

__all__ = [
    "extract_sheets_content",
    "extract_doc_content",
    "extract_thread_content",
    "extract_message_content",
    "parse_message_payload",
    "parse_attachments_from_payload",
    "extract_slides_content",
    "parse_presentation",
]
