"""
Shared attachment filtering logic.

Loads filter rules from config/attachment_filters.json (single source of truth).
Hides trivial attachments like calendar invites, vcards, small images, and
generic filenames from Claude.
"""

import json
import logging
import re
from pathlib import Path
from functools import lru_cache
from typing import Any

_CONFIG_PATH = Path(__file__).parent / "config" / "attachment_filters.json"

# Mirrors config/attachment_filters.json, and a test pins the two equal.
# filters.py is force-included into the wheel; when the JSON is absent from
# the installed layout (the packaging slip that killed glaneur.service
# nightly from 2026-08-08, mise-ditoja), filtering degrades to these defaults
# instead of taking down the caller's whole search with FileNotFoundError.
# The file wins when present, so the JSON stays the single tunable — and only
# a MISSING file falls back; malformed JSON still raises, because that is a
# config error someone made, not a packaging slip.
_DEFAULT_FILTER_CONFIG: dict[str, Any] = {
    "version": 1,
    "description": (
        "Built-in fallback mirror of config/attachment_filters.json. "
        "Calendar invites, vcards, small images, and generic filenames "
        "are hidden from Claude."
    ),
    "image_size_threshold_bytes": 204800,
    "excluded_mime_types": [
        "text/calendar",
        "application/ics",
        "text/vcard",
        "text/x-vcard",
        "image/gif",
    ],
    "excluded_filename_patterns": [
        "^image$",
        "^image\\.(png|jpg|jpeg|gif|webp)$",
        "^image\\d+\\.(png|jpg|jpeg|gif)$",
        "^photo\\.(png|jpg|jpeg|gif|webp)$",
        "^attachment\\.(pdf|docx?|xlsx?)$",
        "^document\\.(pdf|docx?)$",
        "^file\\.(pdf|docx?|xlsx?)$",
        "^untitled",
        "^screenshot\\.(png|jpg)$",
    ],
}


@lru_cache(maxsize=1)
def get_filter_config() -> dict[str, Any]:
    """
    Load filter configuration from JSON file.

    Cached for performance - config doesn't change during runtime.
    Degrades to _DEFAULT_FILTER_CONFIG when the file is absent (a wheel
    install missing the data file), never on malformed content.
    """
    try:
        config: dict[str, Any] = json.loads(_CONFIG_PATH.read_text())
    except FileNotFoundError:
        logging.getLogger(__name__).warning(
            "attachment_filters.json not found at %s — using built-in "
            "default filter config",
            _CONFIG_PATH,
        )
        return dict(_DEFAULT_FILTER_CONFIG)
    return config


@lru_cache(maxsize=1)
def _get_compiled_patterns() -> list[re.Pattern[str]]:
    """
    Pre-compile regex patterns at load time for 3x speedup.

    Benchmarked: 0.267s → 0.089s for 400K matches.
    """
    config = get_filter_config()
    patterns: list[re.Pattern[str]] = []
    for pattern in config.get("excluded_filename_patterns", []):
        try:
            patterns.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            # Invalid regex pattern - skip it
            continue
    return patterns


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
    # Uses pre-compiled patterns for 3x speedup
    for compiled_pattern in _get_compiled_patterns():
        if compiled_pattern.match(name):
            return True

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
