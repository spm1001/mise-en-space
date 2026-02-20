"""
Folder extractor — pure function, no I/O.

Converts a FolderListing into a markdown directory listing.
Subfolders first (with IDs as action targets), files grouped by MIME type.
"""

from collections import defaultdict

from models import FolderListing


def extract_folder_content(listing: FolderListing, title: str = "") -> str:
    """
    Convert a FolderListing into markdown.

    Args:
        listing: FolderListing from adapters.drive.list_folder()
        title: Human-readable folder name — written as H1 heading.

    Returns:
        Markdown string with optional H1, subfolders section, files section.
    """
    lines: list[str] = []

    # --- Title ---
    if title:
        lines.append(f"# {title}")
        lines.append("")

    # --- Subfolders ---
    lines.append("## Subfolders")
    lines.append("")
    if listing.subfolders:
        for sf in listing.subfolders:
            lines.append(f"- {sf.name}/  →  `{sf.id}`")
    else:
        lines.append("**(none)**")
    lines.append("")

    # --- Files ---
    if not listing.files:
        lines.append("## Files")
        lines.append("")
        lines.append("**(none)**")
        lines.append("")
    else:
        # Group by MIME type for compact rendering
        by_type: dict[str, list[str]] = defaultdict(list)
        for f in listing.files:
            by_type[f.mime_type].append(f.name)

        for mime_type, names in sorted(by_type.items()):
            count = len(names)
            lines.append(f"## Files ({count} · {mime_type})")
            lines.append("")
            lines.append(" · ".join(sorted(names)))
            lines.append("")

    # --- Truncation notice ---
    if listing.truncated:
        lines.append(f"> **Note:** Folder has more than {listing.item_count} items — only the first 300 are shown.")
        lines.append("")

    return "\n".join(lines)
