"""
Shared attachment filtering logic.

Loads filter rules from config/attachment_filters.json (single source of truth).
Hides trivial attachments like calendar invites, vcards, small images, and
generic filenames from Claude.
"""

import json
import re
from pathlib import Path
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def get_filter_config() -> dict[str, Any]:
    """
    Load filter configuration from JSON file.

    Cached for performance - config doesn't change during runtime.
    """
    config_path = Path(__file__).parent / "config" / "attachment_filters.json"
    config: dict[str, Any] = json.loads(config_path.read_text())
    return config


def is_trivial_attachment(filename: str, mime_type: str, size: int) -> bool:
    """
    Check if an attachment should be filtered out.

    Trivial attachments are hidden completely from Claude:
    - Calendar invites (.ics)
    - VCards (.vcf)
    - GIFs (typically animated logos/reactions)
    - Small images (<200KB, typically signatures/logos)
    - Generic filenames (image.png, attachment.pdf, etc.)

    Args:
        filename: Attachment filename
        mime_type: MIME type of the attachment
        size: Size in bytes

    Returns:
        True if attachment is trivial (should be filtered), False otherwise
    """
    config = get_filter_config()
    name = (filename or "").lower().strip()

    # Empty filename
    if not name:
        return True

    # Excluded MIME types (calendar invites, vcards, gifs)
    if mime_type in config.get("excluded_mime_types", []):
        return True

    # Excluded filename patterns (generic names like "image.png", "attachment.pdf")
    for pattern in config.get("excluded_filename_patterns", []):
        try:
            if re.match(pattern, name, re.IGNORECASE):
                return True
        except re.error:
            # Invalid regex pattern - skip it
            continue

    # Small images (logos, signatures, inline graphics)
    threshold = config.get("image_size_threshold_bytes", 204800)
    if mime_type and mime_type.startswith("image/") and size < threshold:
        return True

    return False


def filter_attachments(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Filter a list of attachments, removing trivial ones.

    Args:
        attachments: List of attachment dicts with 'filename', 'mime_type'/'mimeType', 'size'

    Returns:
        Filtered list with trivial attachments removed
    """
    filtered = []
    for att in attachments:
        filename = att.get("filename", "")
        # Support both snake_case and camelCase
        mime_type = att.get("mime_type") or att.get("mimeType", "")
        size = att.get("size", 0)

        if not is_trivial_attachment(filename, mime_type, size):
            filtered.append(att)

    return filtered
