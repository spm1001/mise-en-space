"""
Folder extractor — pure function, no I/O.

Converts list_folder() output dict into a markdown directory listing.
Subfolders first (with IDs as action targets), files grouped by MIME type.
"""

from collections import defaultdict


def extract_folder_content(listing: dict) -> str:
    """
    Convert a folder listing dict into markdown.

    Args:
        listing: Dict from adapters.drive.list_folder() with keys:
            subfolders: list of {id, name}
            files: list of {id, name, mimeType}
            file_count: int
            folder_count: int
            truncated: bool

    Returns:
        Markdown string with subfolders section and files section.
    """
    lines: list[str] = []

    # --- Subfolders ---
    lines.append("## Subfolders")
    lines.append("")
    subfolders = listing.get("subfolders", [])
    if subfolders:
        for sf in subfolders:
            lines.append(f"- {sf['name']}/  →  `{sf['id']}`")
    else:
        lines.append("**(none)**")
    lines.append("")

    # --- Files ---
    files = listing.get("files", [])

    if not files:
        lines.append("## Files")
        lines.append("")
        lines.append("**(none)**")
        lines.append("")
    else:
        # Group by MIME type for compact rendering
        by_type: dict[str, list[str]] = defaultdict(list)
        for f in files:
            by_type[f.get("mimeType", "")].append(f.get("name", ""))

        for mime_type, names in sorted(by_type.items()):
            count = len(names)
            label = f"## Files ({count} · {mime_type})" if len(by_type) > 1 else f"## Files ({count})"
            lines.append(label)
            lines.append("")
            lines.append(" · ".join(sorted(names)))
            lines.append("")

    # --- Truncation notice ---
    if listing.get("truncated"):
        item_count = listing.get("item_count", "300+")
        lines.append(f"> **Note:** Folder has more than {item_count} items — only the first 300 are shown.")
        lines.append("")

    return "\n".join(lines)
