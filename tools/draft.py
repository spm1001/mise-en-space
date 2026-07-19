"""
Draft operation — create Gmail drafts via do() verb.

Draft-only: Claude composes, user reviews and sends from Gmail.
Drive file IDs in `include` are resolved to formatted links in the body.
"""

import logging
from html import escape as html_escape
from typing import Any

from adapters.drive import get_file_metadata
from adapters.gmail import (
    IncludedLink,
    create_draft,
    get_draft_headers,
    get_primary_signature,
    update_draft,
)
from html_convert import html_to_text_with_links, markdown_to_html
from models import DoResult, MiseError
from validation import validate_drive_id

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
            validate_drive_id(file_id, "include file_id")
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


def _fetch_signature() -> tuple[str, str, list[str]]:
    """
    Fetch the user's Gmail signature for appending to a draft body.

    Returns (html_part, text_part, warnings) — separator included, empty
    strings when no signature is configured. A signature is a grace note:
    fetch failure produces a warning, never blocks the draft.
    """
    try:
        sig_html = get_primary_signature()
    except MiseError as e:
        return "", "", [f"Could not fetch Gmail signature: {e.message}"]
    except Exception as e:
        return "", "", [f"Could not fetch Gmail signature: {e}"]

    if not sig_html:
        return "", "", []

    sig_text = html_to_text_with_links(sig_html)
    return f"<br>{sig_html}", f"\n\n{sig_text}", []


def _content_to_html(content: str) -> str:
    """
    Render an email draft body (markdown) to HTML.

    Routes through markdown_to_html so GFM tables, **bold**, headings, and
    lists survive into the Gmail draft. The old <p>/<br>-only path emitted
    literal pipe rows and asterisks (field report mise-zolowa). Single
    newlines still become <br> (nl2br) for email-friendly line breaks.
    """
    return markdown_to_html(content)


def do_draft(
    to: str | None = None,
    subject: str | None = None,
    content: str | None = None,
    cc: str | None = None,
    include: list[str] | None = None,
    file_id: str | None = None,
    **_kwargs: Any,
) -> DoResult | dict[str, Any]:
    """
    Create a Gmail draft — or update an existing one in place.

    Draft-only — does NOT send. User reviews and sends from Gmail.
    Drive file IDs in `include` are resolved to formatted links in the body.

    With file_id (a draft ID from a previous draft/reply_draft result):
    updates that draft in place. content is required (the body is rebuilt);
    to/subject/cc not resupplied carry over from the existing draft, and
    threading headers are preserved so reply drafts stay on their thread.

    Args:
        to: Recipient email address(es), comma-separated
        subject: Email subject
        content: Email body text
        cc: Optional CC address(es), comma-separated
        include: Optional list of Drive file IDs to include as links
        file_id: Existing draft ID to update in place (mise-wemuki)

    Returns:
        DoResult on success, error dict on failure
    """
    if file_id:
        return _update_draft_in_place(file_id, to, subject, content, cc, include)

    # Validate required params (create mode)
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

    # Build body: content, then included links, then the Gmail signature
    sig_html, sig_text, sig_warnings = _fetch_signature()
    body_text = content + _format_links_text(included_links) + sig_text
    body_html = _content_to_html(content) + _format_links_html(included_links) + sig_html

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
    if sig_html:
        cues["signature"] = "Gmail signature appended automatically"
    if included_links:
        cues["included_links"] = [
            {"title": l.title, "url": l.web_link} for l in included_links
        ]
    if include_warnings:
        cues["include_warnings"] = include_warnings
    if sig_warnings:
        cues["signature_warnings"] = sig_warnings

    return DoResult(
        file_id=result.draft_id,
        title=subject,
        web_link=result.web_link,
        operation="draft",
        cues=cues,
    )


def _update_draft_in_place(
    draft_id: str,
    to: str | None,
    subject: str | None,
    content: str | None,
    cc: str | None,
    include: list[str] | None,
) -> DoResult | dict[str, Any]:
    """Update an existing draft (drafts.update rebuilds the message wholesale).

    content is required — the body can't be carried over, because Gmail
    stores it as rendered MIME and re-extracting it to re-append links and
    signature would compound conversions. to/subject/cc carry over from the
    draft's current headers when not resupplied; In-Reply-To/References/
    threadId always carry over, so reply drafts stay on their thread.
    """
    if not content:
        return {"error": True, "kind": "invalid_input",
                "message": "draft update (file_id given) requires 'content' — "
                           "the body is rebuilt; to/subject/cc carry over when "
                           "not resupplied"}

    try:
        existing = get_draft_headers(draft_id)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value,
                "message": f"Could not load draft '{draft_id}': {e.message}"}

    headers = existing["headers"]
    carried = [f for f, v in (("to", to), ("subject", subject), ("cc", cc))
               if v is None and headers.get(f)]
    to = to or headers.get("to")
    subject = subject if subject is not None else headers.get("subject", "")
    cc = cc or headers.get("cc")
    if not to:
        return {"error": True, "kind": "invalid_input",
                "message": "draft update requires 'to' — the existing draft "
                           "has no recipient to carry over"}

    included_links: list[IncludedLink] = []
    include_warnings: list[str] = []
    if include:
        included_links, include_warnings = _resolve_include(include)

    sig_html, sig_text, sig_warnings = _fetch_signature()
    body_text = content + _format_links_text(included_links) + sig_text
    body_html = _content_to_html(content) + _format_links_html(included_links) + sig_html

    try:
        result = update_draft(
            draft_id,
            to=to,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            cc=cc,
            thread_id=existing.get("thread_id"),
            in_reply_to=headers.get("in-reply-to"),
            references=headers.get("references"),
        )
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    cues: dict[str, Any] = {
        "action": "Draft updated in place — review and send from Gmail",
    }
    if carried:
        cues["carried_over"] = carried
    if sig_html:
        cues["signature"] = "Gmail signature appended automatically"
    if included_links:
        cues["included_links"] = [
            {"title": l.title, "url": l.web_link} for l in included_links
        ]
    if include_warnings:
        cues["include_warnings"] = include_warnings
    if sig_warnings:
        cues["signature_warnings"] = sig_warnings

    return DoResult(
        file_id=result.draft_id,
        title=subject,
        web_link=result.web_link,
        operation="draft",
        cues=cues,
    )
