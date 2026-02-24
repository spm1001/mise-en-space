"""
Reply draft operation — create threaded Gmail reply drafts via do() verb.

Fetches the thread, infers recipients from the last message, builds
threading headers, and creates a draft that appears in the correct
Gmail conversation. Draft-only: user reviews and sends from Gmail.
"""

import logging
import re
from typing import Any

from adapters.gmail import (
    IncludedLink,
    fetch_thread,
    create_reply_draft,
    _build_references,
    _ensure_re_prefix,
)
from models import DoResult, MiseError
from tools.draft import (
    _content_to_html,
    _format_links_text,
    _format_links_html,
    _resolve_include,
)

logger = logging.getLogger(__name__)

# Regex to extract bare email from "Display Name <email@example.com>" format
_EMAIL_PATTERN = re.compile(r"<([^>]+)>")


def _extract_email(address: str) -> str:
    """Extract bare email from an address that may include a display name."""
    match = _EMAIL_PATTERN.search(address)
    return match.group(1).lower() if match else address.strip().lower()


def _infer_recipients(
    last_message: "EmailMessage",
    authenticated_email: str | None = None,
) -> tuple[str, str | None]:
    """
    Infer reply recipients from the last message in a thread.

    Default (reply): To = sender of the last message.
    Reply-all: To = sender, Cc = original To + Cc minus the authenticated user.

    For now, returns simple reply (to sender only). The tool layer can
    request reply-all by passing reply_all=True.

    Args:
        last_message: The most recent message in the thread.
        authenticated_email: The authenticated user's email (to exclude from Cc).

    Returns:
        (to, cc) — cc is None for simple reply, comma-separated for reply-all.
    """
    to = last_message.from_address
    return to, None


def _infer_recipients_all(
    last_message: "EmailMessage",
    authenticated_email: str | None = None,
) -> tuple[str, str | None]:
    """
    Infer reply-all recipients from the last message.

    To = sender. Cc = all original To + Cc addresses minus the sender
    and the authenticated user.
    """
    sender = last_message.from_address
    sender_email = _extract_email(sender)

    # Collect all addresses from To + Cc, excluding sender and self
    exclude = {sender_email}
    if authenticated_email:
        exclude.add(authenticated_email.lower())

    all_addresses: list[str] = []
    for addr in last_message.to_addresses + last_message.cc_addresses:
        if _extract_email(addr) not in exclude:
            all_addresses.append(addr)

    cc = ", ".join(all_addresses) if all_addresses else None
    return sender, cc


def do_reply_draft(
    file_id: str | None = None,
    content: str | None = None,
    cc: str | None = None,
    include: list[str] | None = None,
    reply_all: bool = False,
    **_kwargs: Any,
) -> DoResult | dict[str, Any]:
    """
    Create a threaded reply draft in Gmail.

    Fetches the thread identified by file_id (thread_id), infers recipients
    from the last message, adds threading headers, and creates a draft in
    the correct conversation. Does NOT send.

    Args:
        file_id: Gmail thread ID to reply to
        content: Reply body text
        cc: Optional explicit CC override (comma-separated). If not provided
            and reply_all=True, Cc is inferred from the thread.
        include: Optional list of Drive file IDs to include as links
        reply_all: If True, infer Cc from all recipients on the last message

    Returns:
        DoResult on success, error dict on failure
    """
    # Validate required params
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "reply_draft requires 'file_id' (Gmail thread ID to reply to)"}
    if not content:
        return {"error": True, "kind": "invalid_input",
                "message": "reply_draft requires 'content' (reply body)"}

    # Fetch the thread to get threading info and recipients
    try:
        thread = fetch_thread(file_id)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    if not thread.messages:
        return {"error": True, "kind": "invalid_input",
                "message": f"Thread {file_id} has no messages"}

    last_message = thread.messages[-1]

    # Infer recipients
    if reply_all:
        to, inferred_cc = _infer_recipients_all(last_message)
    else:
        to, inferred_cc = _infer_recipients(last_message)

    # Explicit cc overrides inferred
    final_cc = cc if cc is not None else inferred_cc

    # Build threading headers
    in_reply_to, references = _build_references(last_message)

    # Subject with Re: prefix
    subject = _ensure_re_prefix(thread.subject)

    # Resolve included Drive links
    included_links: list[IncludedLink] = []
    include_warnings: list[str] = []
    if include:
        included_links, include_warnings = _resolve_include(include)

    # Build body with included links appended
    body_text = content + _format_links_text(included_links)
    body_html = _content_to_html(content) + _format_links_html(included_links)

    try:
        result = create_reply_draft(
            thread_id=file_id,
            to=to,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            in_reply_to=in_reply_to,
            references=references,
            cc=final_cc,
            included_links=included_links,
        )
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    cues: dict[str, Any] = {
        "action": "Reply draft created \u2014 review and send from Gmail",
        "thread_id": file_id,
        "replying_to": last_message.from_address,
    }
    if final_cc:
        cues["cc"] = final_cc
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
        operation="reply_draft",
        cues=cues,
    )