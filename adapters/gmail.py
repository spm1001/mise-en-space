"""
Gmail adapter — Gmail API wrapper.

Fetches threads and messages, parses into typed models.
Creates drafts for the do() verb.
"""

import base64
import logging
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from models import GmailThreadData, GmailSearchResult, EmailMessage, EmailAttachment, ForwardedMessage
from retry import with_retry
from adapters.services import get_gmail_service
from extractors.gmail import parse_message_payload, parse_attachments_from_payload, parse_forwarded_messages
from html_convert import clean_html_for_conversion, convert_html_to_markdown
from filters import is_trivial_attachment, filter_attachments

logger = logging.getLogger(__name__)


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
WANTED_HEADERS = frozenset({
    "From", "To", "Cc", "Subject", "Date",
    "Message-ID", "In-Reply-To", "References",
})

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

    # Pre-convert HTML to markdown if no plain text available.
    # This keeps the extractor layer pure — by the time it sees the
    # EmailMessage, body_text is already populated.
    if body_html and not body_text:
        cleaned = clean_html_for_conversion(body_html)
        body_text, _ = convert_html_to_markdown(cleaned)

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

    # Extract forwarded messages (MIME message/rfc822 parts)
    # The extractor does pure MIME parsing; we handle any HTML conversion here.
    forwarded = parse_forwarded_messages(payload)
    for fwd in forwarded:
        if not fwd.body_text and fwd.body_html:
            cleaned = clean_html_for_conversion(fwd.body_html)
            fwd.body_text, _ = convert_html_to_markdown(cleaned)

    return EmailMessage(
        message_id=msg.get("id", ""),
        from_address=headers.get("From", ""),
        to_addresses=_parse_address_list(headers.get("To")),
        cc_addresses=_parse_address_list(headers.get("Cc")),
        subject=headers.get("Subject", ""),
        date=_parse_date(headers.get("Date"), msg.get("internalDate")),
        body_text=body_text,
        body_html=body_html,
        message_id_header=headers.get("Message-ID"),
        in_reply_to=headers.get("In-Reply-To"),
        references=headers.get("References"),
        attachments=attachments,
        drive_links=drive_links,
        forwarded_messages=forwarded,
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
    # Capture original thread order — batch callbacks arrive in server order, not relevance order
    thread_order = [t["id"] for t in threads[:max_results]]
    results_by_id: dict[str, GmailSearchResult] = {}
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
        if not thread_id:
            logger.warning("Batch callback received response with empty thread_id — skipping")
            return
        results_by_id[thread_id] = GmailSearchResult(
            thread_id=thread_id,
            subject=headers.get("Subject", ""),
            snippet=snippets_by_id.get(thread_id, ""),
            date=_parse_date(headers.get("Date"), first_msg.get("internalDate")),
            from_address=headers.get("From"),
            message_count=len(messages),
            has_attachments=len(attachment_names) > 0,
            attachment_names=attachment_names,
        )

    # Add batch requests — format="full" with fields mask gives us the payload
    # parts tree (for attachment filenames) without message body data.
    # Measured: 5x faster and 5x smaller than unmasked format="full".
    search_fields = (
        "id,messages(id,internalDate,"
        "payload(headers,mimeType,parts(filename,mimeType,body(attachmentId,size))))"
    )
    for thread in threads[:max_results]:
        batch.add(
            service.users()
            .threads()
            .get(userId="me", id=thread["id"], format="full", fields=search_fields),
            callback=handle_thread_response,
        )

    batch.execute()

    # Reorder to match original relevance ranking from threads().list()
    return [results_by_id[tid] for tid in thread_order if tid in results_by_id]


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


# =============================================================================
# DRAFT CREATION
# =============================================================================


@dataclass
class IncludedLink:
    """A Drive file resolved for inclusion in a draft."""
    file_id: str
    title: str
    mime_type: str
    web_link: str


@dataclass
class DraftResult:
    """Result of creating a Gmail draft."""
    draft_id: str
    message_id: str
    web_link: str
    to: str
    subject: str
    included_links: list[IncludedLink] = field(default_factory=list)


def _build_draft_message(
    to: str,
    subject: str,
    body_text: str,
    body_html: str,
    cc: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> str:
    """
    Build RFC 2822 message as base64url string for Gmail API.

    Creates multipart/alternative with text and HTML parts.
    Threading headers (In-Reply-To, References) are added for reply drafts.
    """
    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def _draft_web_link(draft_id: str) -> str:
    """Build Gmail web link for a draft."""
    return f"https://mail.google.com/mail/#drafts/{draft_id}"


@with_retry(max_attempts=3, delay_ms=1000)
def create_draft(
    to: str,
    subject: str,
    body_text: str,
    body_html: str,
    cc: str | None = None,
    included_links: list[IncludedLink] | None = None,
) -> DraftResult:
    """
    Create a Gmail draft.

    Builds an RFC 2822 message with multipart/alternative (text + HTML)
    and creates it as a draft via the Gmail API. Does NOT send.

    Args:
        to: Recipient email address(es), comma-separated
        subject: Email subject
        body_text: Plain text body (with any included links appended)
        body_html: HTML body (with any included links appended)
        cc: Optional CC address(es), comma-separated
        included_links: Resolved Drive links (for result metadata only)

    Returns:
        DraftResult with draft_id, message_id, and web_link

    Raises:
        MiseError: On API failure
    """
    service = get_gmail_service()

    raw = _build_draft_message(to, subject, body_text, body_html, cc=cc)

    draft = (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )

    draft_id = draft["id"]
    message_id = draft.get("message", {}).get("id", "")

    return DraftResult(
        draft_id=draft_id,
        message_id=message_id,
        web_link=_draft_web_link(draft_id),
        to=to,
        subject=subject,
        included_links=included_links or [],
    )


@dataclass
class ReplyDraftResult:
    """Result of creating a threaded reply draft."""
    draft_id: str
    message_id: str
    thread_id: str
    web_link: str
    to: str
    subject: str
    cc: str | None = None
    included_links: list[IncludedLink] = field(default_factory=list)


def _build_references(last_message: EmailMessage) -> tuple[str | None, str | None]:
    """
    Build In-Reply-To and References headers from the last message in a thread.

    RFC 2822: In-Reply-To is the Message-ID of the message being replied to.
    References is the existing References chain plus that Message-ID.

    Returns:
        (in_reply_to, references) — either may be None if no Message-ID available.
    """
    msg_id = last_message.message_id_header
    if not msg_id:
        return None, None

    in_reply_to = msg_id

    # Build References: existing chain + this message's ID
    existing_refs = last_message.references or ""
    if existing_refs:
        references = f"{existing_refs} {msg_id}"
    else:
        references = msg_id

    return in_reply_to, references


def _ensure_re_prefix(subject: str) -> str:
    """Add 'Re: ' prefix if not already present (case-insensitive check)."""
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}"


@with_retry(max_attempts=3, delay_ms=1000)
def create_reply_draft(
    thread_id: str,
    to: str,
    subject: str,
    body_text: str,
    body_html: str,
    in_reply_to: str | None = None,
    references: str | None = None,
    cc: str | None = None,
    included_links: list[IncludedLink] | None = None,
) -> ReplyDraftResult:
    """
    Create a threaded reply draft in Gmail.

    Builds an RFC 2822 message with threading headers and creates it as a
    draft associated with the given thread. Does NOT send.

    Args:
        thread_id: Gmail thread ID to reply to
        to: Recipient email address(es), comma-separated
        subject: Email subject (should already have Re: prefix)
        body_text: Plain text body
        body_html: HTML body
        in_reply_to: Message-ID of the message being replied to
        references: References header chain
        cc: Optional CC address(es), comma-separated
        included_links: Resolved Drive links (for result metadata only)

    Returns:
        ReplyDraftResult with draft_id, thread_id, and web_link

    Raises:
        MiseError: On API failure
    """
    service = get_gmail_service()

    raw = _build_draft_message(
        to, subject, body_text, body_html,
        cc=cc, in_reply_to=in_reply_to, references=references,
    )

    # threadId associates the draft with the existing conversation
    draft = (
        service.users()
        .drafts()
        .create(userId="me", body={
            "message": {"raw": raw, "threadId": thread_id},
        })
        .execute()
    )

    draft_id = draft["id"]
    message_id = draft.get("message", {}).get("id", "")

    return ReplyDraftResult(
        draft_id=draft_id,
        message_id=message_id,
        thread_id=thread_id,
        web_link=_draft_web_link(draft_id),
        to=to,
        subject=subject,
        cc=cc,
        included_links=included_links or [],
    )
