"""
Gmail adapter — Gmail API wrapper.

Fetches threads and messages, parses into typed models.
"""

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from models import GmailThreadData, EmailMessage, EmailAttachment
from retry import with_retry
from adapters.services import get_gmail_service
from extractors.gmail import parse_message_payload, parse_attachments_from_payload


# Fields to request for threads — only what we need
THREAD_FIELDS = (
    "id,"
    "messages("
    "id,"
    "threadId,"
    "labelIds,"
    "payload(headers,mimeType,body,parts),"
    "internalDate"
    ")"
)

# Headers we care about
WANTED_HEADERS = frozenset({"From", "To", "Cc", "Subject", "Date"})

# Regex to find Drive links in text
DRIVE_LINK_PATTERN = re.compile(
    r'https://(?:docs|drive|sheets|slides)\.google\.com/[^\s<>"\']+',
    re.IGNORECASE
)


def _parse_headers(headers: list[dict[str, str]]) -> dict[str, str]:
    """Extract wanted headers into a dict."""
    return {
        h["name"]: h["value"]
        for h in headers
        if h.get("name") in WANTED_HEADERS
    }


def _parse_address_list(value: str | None) -> list[str]:
    """Parse comma-separated email addresses."""
    if not value:
        return []
    # Simple split — doesn't handle quoted names, but good enough
    return [addr.strip() for addr in value.split(",") if addr.strip()]


def _parse_date(date_str: str | None, internal_date: str | None) -> datetime | None:
    """Parse date from header or internal timestamp."""
    if date_str:
        try:
            return parsedate_to_datetime(date_str)
        except (ValueError, TypeError):
            pass

    if internal_date:
        try:
            # internalDate is milliseconds since epoch
            return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
        except (ValueError, TypeError):
            pass

    return None


def _extract_drive_links(text: str | None) -> list[dict[str, str]]:
    """Extract Google Drive/Docs links from text."""
    if not text:
        return []

    links: list[dict[str, str]] = []
    for match in DRIVE_LINK_PATTERN.finditer(text):
        url = match.group(0)
        links.append({"url": url})

    return links


def _build_message(msg: dict[str, Any]) -> EmailMessage:
    """Build EmailMessage from API message response."""
    payload = msg.get("payload", {})
    headers = _parse_headers(payload.get("headers", []))

    # Parse body
    body_text, body_html = parse_message_payload(payload)

    # Parse attachments
    attachments_raw = parse_attachments_from_payload(payload)
    attachments = [
        EmailAttachment(
            filename=a["filename"],
            mime_type=a["mimeType"],
            size=a["size"],
            attachment_id=a["attachment_id"],
        )
        for a in attachments_raw
    ]

    # Extract Drive links from body
    drive_links = _extract_drive_links(body_text) or _extract_drive_links(body_html)

    return EmailMessage(
        message_id=msg.get("id", ""),
        from_address=headers.get("From", ""),
        to_addresses=_parse_address_list(headers.get("To")),
        cc_addresses=_parse_address_list(headers.get("Cc")),
        subject=headers.get("Subject", ""),
        date=_parse_date(headers.get("Date"), msg.get("internalDate")),
        body_text=body_text,
        body_html=body_html,
        attachments=attachments,
        drive_links=drive_links,
    )


@with_retry(max_attempts=3, delay_ms=1000)
def fetch_thread(thread_id: str) -> GmailThreadData:
    """
    Fetch complete thread data.

    Uses threads().get(format='full') to get all messages in one call.
    Full payload includes headers, body, and attachment metadata.

    Args:
        thread_id: The thread ID (from URL or API)

    Returns:
        GmailThreadData ready for the extractor

    Raises:
        MiseError: On API failure (converted by @with_retry)
    """
    service = get_gmail_service()

    # Fetch thread with full message data
    thread = (
        service.users()
        .threads()
        .get(userId="me", id=thread_id, format="full", fields=THREAD_FIELDS)
        .execute()
    )

    # Parse messages
    messages = [_build_message(msg) for msg in thread.get("messages", [])]

    # Get subject from first message
    subject = messages[0].subject if messages else ""

    return GmailThreadData(
        thread_id=thread_id,
        subject=subject,
        messages=messages,
    )


@with_retry(max_attempts=3, delay_ms=1000)
def fetch_message(message_id: str) -> EmailMessage:
    """
    Fetch a single message.

    Args:
        message_id: The message ID

    Returns:
        EmailMessage with full content

    Raises:
        MiseError: On API failure
    """
    service = get_gmail_service()

    msg = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )

    return _build_message(msg)
