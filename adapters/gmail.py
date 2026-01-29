"""
Gmail adapter — Gmail API wrapper.

Fetches threads and messages, parses into typed models.
"""

import base64
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from models import GmailThreadData, GmailSearchResult, EmailMessage, EmailAttachment
from retry import with_retry
from adapters.services import get_gmail_service
from extractors.gmail import parse_message_payload, parse_attachments_from_payload
from filters import is_trivial_attachment, filter_attachments


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

# Fields for search results — lighter than full thread fetch
SEARCH_THREAD_FIELDS = (
    "id,"
    "snippet,"
    "messages(id,payload(headers),internalDate,labelIds)"
)

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

    # Parse attachments (filtered - hide trivials from Claude)
    attachments_raw = parse_attachments_from_payload(payload)
    filtered_raw = filter_attachments(attachments_raw)
    attachments = [
        EmailAttachment(
            filename=a["filename"],
            mime_type=a["mimeType"],
            size=a["size"],
            attachment_id=a["attachment_id"],
        )
        for a in filtered_raw
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


@with_retry(max_attempts=3, delay_ms=1000)
def search_threads(
    query: str,
    max_results: int = 20,
) -> list[GmailSearchResult]:
    """
    Search for threads matching query.

    Uses threads().list() to find threads, then batch-fetches metadata
    for subject, from, date extraction. Returns triage-ready results.

    Args:
        query: Gmail search query (e.g., "from:example@gmail.com budget")
        max_results: Maximum number of results

    Returns:
        List of GmailSearchResult objects

    Raises:
        MiseError: On API failure
    """
    service = get_gmail_service()

    # Step 1: Get thread IDs and snippets
    list_response = (
        service.users()
        .threads()
        .list(userId="me", q=query, maxResults=min(max_results, 100))
        .execute()
    )

    threads = list_response.get("threads", [])
    if not threads:
        return []

    # Preserve snippets from list response — batch fetch with format="metadata" doesn't include them
    snippets_by_id = {t["id"]: t.get("snippet", "") for t in threads}

    # Step 2: Batch-fetch thread metadata for subject/from/date
    # Gmail batch API works (unlike Slides/Sheets/Docs)
    results: list[GmailSearchResult] = []
    batch = service.new_batch_http_request()

    def handle_thread_response(request_id: str, response: dict[str, Any], exception: Exception | None) -> None:
        if exception:
            # Skip failed threads, don't fail entire search
            return

        messages = response.get("messages", [])
        if not messages:
            return

        # First message has subject and sender
        first_msg = messages[0]
        payload = first_msg.get("payload", {})
        headers = _parse_headers(payload.get("headers", []))

        # Collect attachment names from all messages (filtered)
        attachment_names: list[str] = []
        for msg in messages:
            msg_payload = msg.get("payload", {})
            msg_attachments = parse_attachments_from_payload(msg_payload)
            # Filter out trivial attachments
            filtered = filter_attachments(msg_attachments)
            for att in filtered:
                if att.get("filename"):
                    attachment_names.append(att["filename"])

        thread_id = response.get("id", "")
        results.append(
            GmailSearchResult(
                thread_id=thread_id,
                subject=headers.get("Subject", ""),
                snippet=snippets_by_id.get(thread_id, ""),
                date=_parse_date(headers.get("Date"), first_msg.get("internalDate")),
                from_address=headers.get("From"),
                message_count=len(messages),
                has_attachments=len(attachment_names) > 0,
                attachment_names=attachment_names,
            )
        )

    # Add batch requests — use format="full" to get attachment info
    for thread in threads[:max_results]:
        batch.add(
            service.users()
            .threads()
            .get(userId="me", id=thread["id"], format="full"),
            callback=handle_thread_response,
        )

    batch.execute()

    return results


# =============================================================================
# ATTACHMENT DOWNLOAD
# =============================================================================


@dataclass
class AttachmentDownload:
    """Result of downloading an attachment."""
    filename: str
    mime_type: str
    size: int
    content: bytes
    temp_path: Path | None = None  # For large files streamed to disk


# Large attachment threshold (50MB) - stream to disk above this
ATTACHMENT_STREAMING_THRESHOLD = 50 * 1024 * 1024


@with_retry(max_attempts=3, delay_ms=1000)
def download_attachment(
    message_id: str,
    attachment_id: str,
    filename: str = "",
    mime_type: str = "",
) -> AttachmentDownload:
    """
    Download a Gmail attachment.

    For small attachments, returns content in memory.
    For large attachments (>50MB), streams to temp file.

    Args:
        message_id: Gmail message ID containing the attachment
        attachment_id: Attachment ID from message payload
        filename: Optional filename (for result metadata)
        mime_type: Optional MIME type (for result metadata)

    Returns:
        AttachmentDownload with content bytes (or temp_path for large files)

    Raises:
        MiseError: On API failure
    """
    service = get_gmail_service()

    # Download attachment data
    response = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )

    # Decode base64url data
    data = base64.urlsafe_b64decode(response["data"])
    size = len(data)

    # For large files, write to temp
    temp_path = None
    if size > ATTACHMENT_STREAMING_THRESHOLD:
        suffix = ""
        if filename and "." in filename:
            suffix = "." + filename.rsplit(".", 1)[1]
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(data)
        tmp.close()
        temp_path = Path(tmp.name)

    return AttachmentDownload(
        filename=filename or "attachment",
        mime_type=mime_type or "application/octet-stream",
        size=size,
        content=data,
        temp_path=temp_path,
    )
