"""
Folder extractor — pure function, no I/O.

Converts a FolderListing into a markdown directory listing.
Subfolders first (with IDs as action targets), files grouped by MIME type.

Also handles recursive FolderTreeNode → indented tree rendering.
"""

from collections import defaultdict

from models import FolderListing, FolderTreeNode


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


def extract_folder_tree(tree: FolderTreeNode) -> str:
    """
    Convert a recursive FolderTreeNode into an indented tree markdown.

    Each level is indented with 2 spaces. Folders show their ID for
    action targeting (move, fetch). Files are listed under their folder.

    Args:
        tree: FolderTreeNode from adapters.drive.list_folder_recursive()

    Returns:
        Markdown string with tree structure.
    """
    lines: list[str] = []
    lines.append(f"# {tree.name}")
    lines.append("")
    _render_tree_node(tree, lines, depth=0)
    return "\n".join(lines)


def _render_tree_node(node: FolderTreeNode, lines: list[str], depth: int) -> None:
    """Recursively render a tree node with indentation."""
    indent = "  " * depth

    # Files at this level
    for f in node.listing.files:
        lines.append(f"{indent}- {f.name}")

    # Child folders (recursive)
    for child in node.children:
        lines.append(f"{indent}- {child.name}/  `{child.id}`")
        _render_tree_node(child, lines, depth + 1)

    # Subfolders that weren't traversed (no children but exist in listing)
    traversed_ids = {c.id for c in node.children}
    for sf in node.listing.subfolders:
        if sf.id not in traversed_ids:
            lines.append(f"{indent}- {sf.name}/  `{sf.id}`  *(not traversed)*")

    if node.depth_truncated and not node.children:
        lines.append(f"{indent}  *(depth limit reached)*")

    if node.listing.truncated:
        lines.append(f"{indent}  *(truncated — more items exist)*")
