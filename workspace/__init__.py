"""
Workspace — Per-session folder management.

Handles file deposit to mise/{type}--{title}--{id}/ folders.
Filesystem-first pattern: content goes to disk, Claude reads what it needs.
"""

from .manager import (
    slugify,
    get_deposit_folder,
    write_content,
    write_thumbnail,
    write_page_thumbnail,
    write_image,
    write_chart,
    write_charts_metadata,
    write_manifest,
    enrich_manifest,
)

__all__ = [
    "slugify",
    "get_deposit_folder",
    "write_content",
    "write_thumbnail",
    "write_page_thumbnail",
    "write_image",
    "write_chart",
    "write_charts_metadata",
    "write_manifest",
    "enrich_manifest",
]
