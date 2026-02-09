"""
Shared helpers for fetch sub-modules.

Contains _build_cues, _build_email_context_metadata,
_enrich_with_comments, and text file detection.
"""

from pathlib import Path
from typing import Any

from adapters.drive import fetch_file_comments
from extractors.comments import extract_comments_content
from models import MiseError, EmailContext
from workspace import write_content


def _enrich_with_comments(file_id: str, folder: Path) -> tuple[int, str | None]:
    """
    Fetch open comments and write to deposit folder.

    Sous-chef philosophy: bring everything chef needs without being asked.

    Args:
        file_id: Drive file ID
        folder: Deposit folder path

    Returns:
        Tuple of (open_comment_count, comments_md or None)
        Fails silently — comments are optional enrichment.
    """
    try:
        data = fetch_file_comments(file_id, include_resolved=False, max_results=100)
        if not data.comments:
            return (0, None)

        # Extract to markdown
        comments_md = extract_comments_content(data)

        # Write to deposit folder
        write_content(folder, comments_md, filename="comments.md")

        return (data.comment_count, comments_md)
    except MiseError:
        return (0, None)
    except Exception:
        return (0, None)


def _build_cues(
    folder: Path | str,
    *,
    open_comment_count: int = 0,
    warnings: list[str] | None = None,
    email_context: EmailContext | None = None,
    participants: list[str] | None = None,
    has_attachments: bool | None = None,
    date_range: str | None = None,
) -> dict[str, Any]:
    """
    Build cues dict for FetchResult — decision-tree signals for the caller.

    Cues surface actionable information so callers don't need to read
    manifest.json or Glob the deposit folder. Explicit nulls mean
    "we checked, nothing found" (not "we didn't check").
    """
    folder_path = Path(folder) if isinstance(folder, str) else folder

    # Single pass: list files and find content length
    file_names: list[str] = []
    thumbnail_names: list[str] = []
    content_length = 0
    if folder_path.exists():
        for f in folder_path.iterdir():
            if f.is_file():
                name = f.name
                if name.startswith("slide_") and name.endswith(".png"):
                    thumbnail_names.append(name)
                else:
                    file_names.append(name)
                if name.startswith("content."):
                    content_length = f.stat().st_size

    # Collapse thumbnails into a compact summary
    files = sorted(file_names)
    if thumbnail_names:
        sorted_thumbs = sorted(thumbnail_names)
        if len(sorted_thumbs) > 3:
            files.append(f"{sorted_thumbs[0]} ... {sorted_thumbs[-1]} ({len(sorted_thumbs)} thumbnails)")
        else:
            files.extend(sorted_thumbs)

    cues: dict[str, Any] = {
        "files": files,
        "open_comment_count": open_comment_count,
        "warnings": warnings or [],
        "content_length": content_length,
        "email_context": (
            _build_email_context_metadata(email_context) if email_context else None
        ),
    }

    # Gmail-specific cues
    if participants is not None:
        cues["participants"] = participants
    if has_attachments is not None:
        cues["has_attachments"] = has_attachments
    if date_range is not None:
        cues["date_range"] = date_range

    return cues


# Text MIME types that can be downloaded and deposited directly
TEXT_MIME_TYPES = {
    "text/plain",
    "text/csv",
    "text/markdown",
    "text/html",
    "text/xml",
    "application/json",
    "application/xml",
    "application/x-yaml",
    "text/x-python",
    "text/javascript",
    "application/javascript",
}


def is_text_file(mime_type: str) -> bool:
    """Check if MIME type is a text-based format we can handle directly."""
    if mime_type in TEXT_MIME_TYPES:
        return True
    # Also handle any text/* type not explicitly listed
    if mime_type.startswith("text/"):
        return True
    return False


def _build_email_context_metadata(email_context: EmailContext | None) -> dict[str, Any] | None:
    """Build email_context dict for FetchResult metadata."""
    if not email_context:
        return None
    return {
        "message_id": email_context.message_id,
        "from": email_context.from_address,
        "subject": email_context.subject,
        "hint": f"Use fetch('{email_context.message_id}') to get source email",
    }
