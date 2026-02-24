"""
Draft operation — create Gmail drafts via do() verb.

Draft-only: Claude composes, user reviews and sends from Gmail.
Drive file IDs in `include` are resolved to formatted links in the body.
"""

import logging
from html import escape as html_escape
from typing import Any

from adapters.drive import get_file_metadata
from adapters.gmail import create_draft, IncludedLink
from models import DoResult, MiseError

logger = logging.getLogger(__name__)

# MIME types for Google-native files (always linked, never "attached")
_GOOGLE_NATIVE_MIME_ICONS: dict[str, str] = {
    "application/vnd.google-apps.document": "\U0001f4dd",      # 📝
    "application/vnd.google-apps.spreadsheet": "\U0001f4ca",    # 📊
    "application/vnd.google-apps.presentation": "\U0001f4fd",   # 📽
    "application/vnd.google-apps.form": "\U0001f4cb",           # 📋
    "application/vnd.google-apps.drawing": "\U0001f3a8",        # 🎨
}

# Fallback icon for non-native Drive files (PDFs, images, etc.)
_DEFAULT_FILE_ICON = "\U0001f4ce"  # 📎


def _icon_for_mime(mime_type: str) -> str:
    """Return an emoji icon for a MIME type."""
    return _GOOGLE_NATIVE_MIME_ICONS.get(mime_type, _DEFAULT_FILE_ICON)


def _resolve_include(file_ids: list[str]) -> tuple[list[IncludedLink], list[str]]:
    """
    Resolve Drive file IDs to link metadata.

    Returns (resolved_links, warnings). Failures are warnings, not errors —
    a bad include ID shouldn't block the draft.
    """
    links: list[IncludedLink] = []
    warnings: list[str] = []

    for file_id in file_ids:
        try:
            meta = get_file_metadata(file_id)
            links.append(IncludedLink(
                file_id=file_id,
                title=meta.get("name", file_id),
                mime_type=meta.get("mimeType", ""),
                web_link=meta.get("webViewLink", ""),
            ))
        except MiseError as e:
            warnings.append(f"Could not resolve include '{file_id}': {e.message}")
        except Exception as e:
            warnings.append(f"Could not resolve include '{file_id}': {str(e)}")

    return links, warnings


def _format_links_text(links: list[IncludedLink]) -> str:
    """Format included links for plain text body."""
    if not links:
        return ""

    lines = ["", ""]  # Two newlines before links section
    for link in links:
        icon = _icon_for_mime(link.mime_type)
        lines.append(f"{icon} {link.title}")
        lines.append(f"  {link.web_link}")

    return "\n".join(lines)


def _format_links_html(links: list[IncludedLink]) -> str:
    """Format included links for HTML body."""
    if not links:
        return ""

    parts = ['<br><hr style="border:none;border-top:1px solid #ddd;margin:16px 0">']
    for link in links:
        icon = _icon_for_mime(link.mime_type)
        title = html_escape(link.title)
        url = html_escape(link.web_link)
        parts.append(
            f'<p>{icon} <a href="{url}">{title}</a></p>'
        )

    return "\n".join(parts)


def _content_to_html(content: str) -> str:
    """
    Convert plain text content to simple HTML.

    Preserves line breaks as <br> and paragraphs as <p> blocks.
    Does NOT attempt full markdown->HTML conversion — this is email,
    not a document. Keep it simple and predictable.
    """
    # Split on double newlines for paragraphs
    paragraphs = content.split("\n\n")
    html_parts = []
    for para in paragraphs:
        # Escape HTML entities, convert single newlines to <br>
        escaped = html_escape(para.strip())
        escaped = escaped.replace("\n", "<br>\n")
        if escaped:
            html_parts.append(f"<p>{escaped}</p>")

    return "\n".join(html_parts)


def do_draft(
    to: str | None = None,
    subject: str | None = None,
    content: str | None = None,
    cc: str | None = None,
    include: list[str] | None = None,
    **_kwargs: Any,
) -> DoResult | dict[str, Any]:
    """
    Create a Gmail draft.

    Draft-only — does NOT send. User reviews and sends from Gmail.
    Drive file IDs in `include` are resolved to formatted links in the body.

    Args:
        to: Recipient email address(es), comma-separated
        subject: Email subject
        content: Email body text
        cc: Optional CC address(es), comma-separated
        include: Optional list of Drive file IDs to include as links

    Returns:
        DoResult on success, error dict on failure
    """
    # Validate required params
    if not to:
        return {"error": True, "kind": "invalid_input",
                "message": "draft requires 'to' (recipient email address)"}
    if not subject:
        return {"error": True, "kind": "invalid_input",
                "message": "draft requires 'subject'"}
    if not content:
        return {"error": True, "kind": "invalid_input",
                "message": "draft requires 'content' (email body)"}

    # Resolve included Drive links
    included_links: list[IncludedLink] = []
    include_warnings: list[str] = []
    if include:
        included_links, include_warnings = _resolve_include(include)

    # Build body with included links appended
    body_text = content + _format_links_text(included_links)
    body_html = _content_to_html(content) + _format_links_html(included_links)

    try:
        result = create_draft(
            to=to,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            cc=cc,
            included_links=included_links,
        )
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    cues: dict[str, Any] = {
        "action": "Draft created \u2014 review and send from Gmail",
    }
    if included_links:
        cues["included_links"] = [
            {"title": l.title, "url": l.web_link} for l in included_links
        ]
    if include_warnings:
        cues["include_warnings"] = include_warnings

    return DoResult(
        file_id=result.draft_id,
        title=subject,
        web_link=result.web_link,
        operation="draft",
        cues=cues,
    )
