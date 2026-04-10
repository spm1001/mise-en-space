"""
Gmail adapter — Gmail API wrapper.

Fetches threads and messages, parses into typed models.
Creates drafts and reply drafts for the do() verb.
Modifies threads (archive, label, star) for the do() verb.

Uses httpx via MiseSyncClient (Phase 1 migration). Will switch to
MiseHttpClient (async) when the tools/server layer goes async.
"""

import base64
import logging
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Any

from models import GmailThreadData, GmailSearchResult, GmailSearchResults, EmailMessage, EmailAttachment, ForwardedMessage
from retry import with_retry
from adapters.http_client import get_sync_client
from extractors.gmail import parse_message_payload, parse_attachments_from_payload, parse_forwarded_messages
from html_convert import clean_html_for_conversion, convert_html_to_markdown
from filters import is_trivial_attachment, filter_attachments

logger = logging.getLogger(__name__)


# Gmail API v1 base URL
_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

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
    """Parse email addresses from a header value, handling RFC 5322 edge cases.

    Uses email.utils.getaddresses to correctly handle commas inside quoted
    display names (e.g., "Doe, Jane" <jane@example.com>).
    """
    if not value:
        return []
    # Strip trailing comma — sloppy clients emit it, getaddresses misparses it
    cleaned = value.strip().rstrip(",")
    if not cleaned:
        return []
    parsed = getaddresses([cleaned])
    return [formataddr(pair) if pair[0] else pair[1]
            for pair in parsed if pair[1]]


def _parse_date(date_str: str | None, internal_date: str | None) -> datetime | None:
    """Parse date from header or internal timestamp.

    Always returns timezone-aware datetimes (UTC assumed for naive dates)
    to avoid 'can't compare offset-naive and offset-aware datetimes' errors
    when min()/max() is called on a list of dates from mixed sources.
    """
    if date_str:
        try:
            dt = parsedate_to_datetime(date_str)
            # Some Date headers lack timezone — make them UTC to avoid
            # naive vs aware comparison errors downstream
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
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
        label_ids=msg.get("labelIds", []),
    )


@with_retry(max_attempts=3, delay_ms=1000)
def fetch_thread(thread_id: str) -> GmailThreadData:
    """
    Fetch complete thread data.

    Uses threads.get with format=full to get all messages in one call.
    Full payload includes headers, body, and attachment metadata.

    Args:
        thread_id: The thread ID (from URL or API)

    Returns:
        GmailThreadData ready for the extractor

    Raises:
        MiseError: On API failure (converted by @with_retry)
    """
    client = get_sync_client()

    thread = client.get_json(
        f"{_GMAIL_API}/threads/{thread_id}",
        params={"format": "full", "fields": THREAD_FIELDS},
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
    client = get_sync_client()

    msg = client.get_json(
        f"{_GMAIL_API}/messages/{message_id}",
        params={"format": "full"},
    )

    return _build_message(msg)


@with_retry(max_attempts=3, delay_ms=1000)
def search_threads(
    query: str,
    max_results: int = 20,
) -> GmailSearchResults:
    """
    Search for threads matching query.

    Uses threads.list to find threads, then fetches metadata for each
    individually for subject, from, date extraction. Returns triage-ready results.

    Paginates through threads.list using nextPageToken until max_results
    threads are collected or no more pages exist. Gmail API caps each page
    at 100 threads, so queries requesting >100 results require multiple pages.

    Args:
        query: Gmail search query (e.g., "from:example@gmail.com budget")
        max_results: Maximum number of results

    Returns:
        GmailSearchResults with results list and truncated flag

    Raises:
        MiseError: On API failure
    """
    client = get_sync_client()

    # Step 1: Collect thread IDs across pages (Gmail caps at 100 per page)
    threads: list[dict[str, Any]] = []
    page_token: str | None = None
    truncated = False

    while len(threads) < max_results:
        remaining = max_results - len(threads)
        params: dict[str, Any] = {
            "q": query,
            "maxResults": min(remaining, 100),
        }
        if page_token:
            params["pageToken"] = page_token

        list_response = client.get_json(
            f"{_GMAIL_API}/threads",
            params=params,
        )

        page_threads = list_response.get("threads", [])
        if not page_threads:
            break

        threads.extend(page_threads)
        page_token = list_response.get("nextPageToken")

        if not page_token:
            break

    # If we hit max_results and there's still a nextPageToken, results are truncated
    if page_token and len(threads) >= max_results:
        truncated = True

    threads = threads[:max_results]

    if not threads:
        return GmailSearchResults(results=[], truncated=False)

    # Preserve snippets from list response — individual fetch with fields mask doesn't include them
    snippets_by_id = {t["id"]: t.get("snippet", "") for t in threads}

    # Step 2: Fetch thread metadata individually
    # Fields mask gives us payload parts tree (for attachment filenames)
    # without message body data. Same fields as the old batch request.
    search_fields = (
        "id,messages(id,internalDate,labelIds,"
        "payload(headers,mimeType,parts(filename,mimeType,body(attachmentId,size))))"
    )

    results: list[GmailSearchResult] = []
    for thread in threads:
        thread_id = thread["id"]
        try:
            response = client.get_json(
                f"{_GMAIL_API}/threads/{thread_id}",
                params={"format": "full", "fields": search_fields},
            )
        except Exception:
            # Skip failed threads, don't fail entire search
            continue

        messages = response.get("messages", [])
        if not messages:
            continue

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

        resp_thread_id = response.get("id", "")
        if not resp_thread_id:
            logger.warning("Thread response with empty thread_id — skipping")
            continue

        # Collect label IDs from first message (thread-level view)
        first_label_ids = first_msg.get("labelIds", [])
        # Thread is unread if any message has UNREAD label
        thread_is_unread = any(
            "UNREAD" in msg.get("labelIds", []) for msg in messages
        )

        results.append(GmailSearchResult(
            thread_id=resp_thread_id,
            subject=headers.get("Subject", ""),
            snippet=snippets_by_id.get(resp_thread_id, ""),
            date=_parse_date(headers.get("Date"), first_msg.get("internalDate")),
            from_address=headers.get("From"),
            message_count=len(messages),
            has_attachments=len(attachment_names) > 0,
            attachment_names=attachment_names,
            is_unread=thread_is_unread,
            label_ids=first_label_ids,
        ))

    return GmailSearchResults(results=results, truncated=truncated)


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
    client = get_sync_client()

    # Download attachment data
    response = client.get_json(
        f"{_GMAIL_API}/messages/{message_id}/attachments/{attachment_id}",
    )

    # Decode base64url data
    data = base64.urlsafe_b64decode(response["data"])
    size = len(data)

    # For large files, write to temp and release memory
    temp_path = None
    if size > ATTACHMENT_STREAMING_THRESHOLD:
        suffix = ""
        if filename and "." in filename:
            suffix = "." + filename.rsplit(".", 1)[1]
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(data)
        tmp.close()
        temp_path = Path(tmp.name)
        data = b""  # Release decoded bytes — content lives on disk now

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
    client = get_sync_client()

    raw = _build_draft_message(to, subject, body_text, body_html, cc=cc)

    draft = client.post_json(
        f"{_GMAIL_API}/drafts",
        json_body={"message": {"raw": raw}},
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
    client = get_sync_client()

    raw = _build_draft_message(
        to, subject, body_text, body_html,
        cc=cc, in_reply_to=in_reply_to, references=references,
    )

    # threadId associates the draft with the existing conversation
    draft = client.post_json(
        f"{_GMAIL_API}/drafts",
        json_body={"message": {"raw": raw, "threadId": thread_id}},
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


# =============================================================================
# THREAD MODIFICATION (archive, label, star)
# =============================================================================


# System labels have fixed IDs — no resolution needed
SYSTEM_LABELS = {
    "INBOX": "INBOX",
    "STARRED": "STARRED",
    "UNREAD": "UNREAD",
    "IMPORTANT": "IMPORTANT",
    "SENT": "SENT",
    "DRAFT": "DRAFT",
    "SPAM": "SPAM",
    "TRASH": "TRASH",
    "CATEGORY_PERSONAL": "CATEGORY_PERSONAL",
    "CATEGORY_SOCIAL": "CATEGORY_SOCIAL",
    "CATEGORY_PROMOTIONS": "CATEGORY_PROMOTIONS",
    "CATEGORY_UPDATES": "CATEGORY_UPDATES",
    "CATEGORY_FORUMS": "CATEGORY_FORUMS",
}


@dataclass
class ModifyThreadResult:
    """Result of modifying a Gmail thread's labels."""
    thread_id: str
    added_labels: list[str]
    removed_labels: list[str]


@with_retry(max_attempts=3, delay_ms=1000)
def list_labels() -> list[dict[str, str]]:
    """
    List all Gmail labels (system + user).

    Returns:
        List of dicts with 'id', 'name', 'type' keys.
    """
    client = get_sync_client()
    response = client.get_json(f"{_GMAIL_API}/labels")
    return [
        {"id": l["id"], "name": l["name"], "type": l.get("type", "system")}
        for l in response.get("labels", [])
    ]


def resolve_label_name(name: str) -> str:
    """
    Resolve a human-readable label name to a Gmail label ID.

    System labels (INBOX, STARRED, etc.) are returned as-is.
    User labels are looked up via labels.list — case-insensitive match.

    Args:
        name: Label name (e.g., "Projects/Active", "INBOX")

    Returns:
        Label ID string

    Raises:
        MiseError: If label not found
    """
    from models import MiseError, ErrorKind

    # Check system labels first (case-insensitive)
    upper = name.upper()
    if upper in SYSTEM_LABELS:
        return SYSTEM_LABELS[upper]

    # Fetch user labels and match by name
    labels = list_labels()

    # Case-insensitive match on name
    name_lower = name.lower()
    for label in labels:
        if label["name"].lower() == name_lower:
            return label["id"]

    raise MiseError(
        ErrorKind.NOT_FOUND,
        f"Label '{name}' not found. Available labels: {sorted(l['name'] for l in labels if l['type'] == 'user')}",
    )


@with_retry(max_attempts=3, delay_ms=1000)
def modify_thread(
    thread_id: str,
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
) -> ModifyThreadResult:
    """
    Modify labels on a Gmail thread.

    This is the primitive that archive, star, and label operations use.
    Archive = remove INBOX. Star = add STARRED. Label = add/remove by ID.

    Args:
        thread_id: Gmail thread ID
        add_label_ids: Label IDs to add
        remove_label_ids: Label IDs to remove

    Returns:
        ModifyThreadResult with the labels that were changed

    Raises:
        MiseError: On API failure
    """
    client = get_sync_client()

    body: dict[str, Any] = {}
    if add_label_ids:
        body["addLabelIds"] = add_label_ids
    if remove_label_ids:
        body["removeLabelIds"] = remove_label_ids

    client.post_json(
        f"{_GMAIL_API}/threads/{thread_id}/modify",
        json_body=body,
    )

    return ModifyThreadResult(
        thread_id=thread_id,
        added_labels=add_label_ids or [],
        removed_labels=remove_label_ids or [],
    )
