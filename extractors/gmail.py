"""
Gmail Extractor — Pure functions for converting Gmail data to text.

Receives GmailThreadData/EmailMessage dataclasses, returns text output.
No API calls, no MCP awareness.
"""

import base64
import re
import tempfile
import os
from datetime import datetime
from typing import Any

from models import GmailThreadData, EmailMessage

from .talon_signature import strip_signature_and_quotes


# =============================================================================
# HTML CLEANING
# =============================================================================


def _clean_html_for_conversion(html: str) -> str:
    """
    Strip common email HTML cruft before markdown conversion.

    Email HTML is notoriously messy - this pre-filter removes patterns
    that cause artifacts in markdown conversion.
    """
    if not html:
        return html

    # Hidden line breaks (Adobe's anti-tracking trick: 7.<br style="display:none"/>1.<br/>26)
    html = re.sub(
        r'<br\s+style="[^"]*display:\s*none[^"]*"\s*/?>',
        '',
        html,
        flags=re.IGNORECASE
    )

    # MSO conditionals (Outlook-specific blocks)
    html = re.sub(
        r'<!--\[if\s+.*?\]>.*?<!\[endif\]-->',
        '',
        html,
        flags=re.DOTALL | re.IGNORECASE
    )

    # Tracking pixels (1x1 images)
    html = re.sub(
        r'<img[^>]*(?:width|height)=["\']1["\'][^>]*/?>',
        '',
        html,
        flags=re.IGNORECASE
    )

    # Completely hidden elements (display:none)
    html = re.sub(
        r'<[^>]+style="[^"]*display:\s*none[^"]*"[^>]*>.*?</[^>]+>',
        '',
        html,
        flags=re.DOTALL | re.IGNORECASE
    )

    # Spacer cells with just &nbsp;
    html = re.sub(
        r'<td[^>]*>\s*(&nbsp;|\s)*\s*</td>',
        '',
        html,
        flags=re.IGNORECASE
    )

    # Empty paragraphs and divs (collapse whitespace)
    html = re.sub(
        r'<(p|div)[^>]*>\s*(&nbsp;|\s)*\s*</\1>',
        '',
        html,
        flags=re.IGNORECASE
    )

    return html


def _convert_html_to_markdown(html: str) -> tuple[str, bool]:
    """
    Convert HTML to markdown using markitdown (local, fast).

    Previous approach used Google Docs API as intermediary (create temp doc,
    export as markdown, delete). That was ~10s for a 64KB email. markitdown
    does the same conversion locally in ~100ms — 98x faster.

    Falls back to basic HTML tag stripping if markitdown fails.

    Args:
        html: HTML content to convert

    Returns:
        Tuple of (markdown_string, used_fallback)
        used_fallback is True if markitdown failed and we stripped tags
    """
    if not html or not html.strip():
        return '', False

    try:
        from markitdown import MarkItDown

        # markitdown needs a file, so write to temp
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.html', delete=False, encoding='utf-8'
        ) as f:
            f.write(html)
            temp_path = f.name

        try:
            md = MarkItDown()
            result = md.convert(temp_path)
            markdown = result.text_content if result else ''

            if markdown:
                return markdown, False
            else:
                raise ValueError("markitdown returned empty result")

        finally:
            # Clean up temp file
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    except Exception:
        # Fallback: basic HTML tag stripping
        text = re.sub(r'<[^>]+>', ' ', html)  # Remove HTML tags
        text = re.sub(r'\s+', ' ', text)  # Collapse whitespace
        return text.strip(), True


# =============================================================================
# DRIVE LINK EXTRACTION
# =============================================================================


def _extract_drive_links(text: str) -> list[dict[str, str]]:
    """
    Extract Google Drive/Docs links from text.

    People often say "attached" when they mean "linked". This extracts
    Drive file IDs so they can be surfaced alongside real attachments.

    Returns:
        List of dicts with 'file_id' and 'url' keys
    """
    if not text:
        return []

    # Patterns for various Google Docs URLs
    patterns = [
        # drive.google.com/open?id=XXX
        r'https?://drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)',
        # drive.google.com/file/d/XXX
        r'https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)',
        # drive.google.com/drive/folders/XXX
        r'https?://drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)',
        # docs.google.com/document/d/XXX
        r'https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)',
        # docs.google.com/spreadsheets/d/XXX
        r'https?://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)',
        # docs.google.com/presentation/d/XXX
        r'https?://docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)',
        # docs.google.com/forms/d/XXX
        r'https?://docs\.google\.com/forms/d/([a-zA-Z0-9_-]+)',
        # docs.google.com/drawings/d/XXX
        r'https?://docs\.google\.com/drawings/d/([a-zA-Z0-9_-]+)',
        # sites.google.com/d/XXX (new Sites)
        r'https?://sites\.google\.com/d/([a-zA-Z0-9_-]+)',
    ]

    links: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for pattern in patterns:
        for match in re.finditer(pattern, text):
            file_id = match.group(1)
            if file_id not in seen_ids:
                seen_ids.add(file_id)
                links.append({
                    'file_id': file_id,
                    'url': match.group(0)
                })

    return links


# =============================================================================
# MESSAGE EXTRACTION
# =============================================================================


def extract_message_content(
    message: EmailMessage,
    strip_signature: bool = True,
) -> tuple[str, list[str]]:
    """
    Extract clean text content from a single email message.

    Args:
        message: EmailMessage with body_text and/or body_html
        strip_signature: Whether to strip signatures and quoted replies

    Returns:
        Tuple of (clean_text_content, warnings_list)
        Prefers plain text over HTML conversion.
    """
    warnings: list[str] = []

    # Prefer plain text if available
    if message.body_text:
        body = message.body_text
    elif message.body_html:
        # Clean and convert HTML
        cleaned_html = _clean_html_for_conversion(message.body_html)
        body, used_fallback = _convert_html_to_markdown(cleaned_html)
        if used_fallback:
            warnings.append("HTML conversion failed, used basic tag stripping")
    else:
        warnings.append("Message has no body content")
        return '', warnings

    # Strip signatures and quoted replies if requested
    if strip_signature and body:
        body = strip_signature_and_quotes(body)

    return body.strip(), warnings


def _format_message_header(message: EmailMessage, position: int, total: int) -> str:
    """Format message header for thread assembly."""
    parts = []

    # Position indicator
    parts.append(f"[{position}/{total}]")

    # From
    parts.append(f"From: {message.from_address}")

    # Date
    if message.date:
        if isinstance(message.date, datetime):
            parts.append(f"Date: {message.date.strftime('%Y-%m-%d %H:%M')}")
        else:
            parts.append(f"Date: {message.date}")

    # Subject (only if different from thread subject, or first message)
    if position == 1 and message.subject:
        parts.append(f"Subject: {message.subject}")

    return " | ".join(parts)


# =============================================================================
# THREAD EXTRACTION
# =============================================================================


def extract_thread_content(
    data: GmailThreadData,
    max_length: int | None = None,
    strip_signatures: bool = True,
) -> str:
    """
    Convert Gmail thread data to markdown text.

    Populates data.warnings with extraction issues encountered.

    Args:
        data: GmailThreadData with subject and messages
        max_length: Optional character limit. Truncates if exceeded.
        strip_signatures: Whether to strip signatures from each message

    Returns:
        Formatted thread content with message headers and clean body text.
        Format:
            # Subject Line

            [1/3] From: alice@example.com | Date: 2024-01-15 10:30 | Subject: Re: Meeting

            Message body here...

            ---

            [2/3] From: bob@example.com | Date: 2024-01-15 11:45

            Reply body here...
    """
    content_parts: list[str] = []
    total_length = 0
    total_messages = len(data.messages)

    # Clear any existing warnings
    data.warnings = []

    # Thread header
    header = f"# {data.subject}\n\n"
    content_parts.append(header)
    total_length += len(header)

    # Process each message
    truncated = False
    for i, message in enumerate(data.messages, start=1):
        # Message separator (except for first)
        if i > 1:
            sep = "\n---\n\n"
            if max_length and (total_length + len(sep)) > max_length:
                truncated = True
                break
            content_parts.append(sep)
            total_length += len(sep)

        # Message header
        msg_header = _format_message_header(message, i, total_messages)
        msg_header_block = f"{msg_header}\n\n"

        if max_length and (total_length + len(msg_header_block)) > max_length:
            truncated = True
            break

        content_parts.append(msg_header_block)
        total_length += len(msg_header_block)

        # Message body
        body, msg_warnings = extract_message_content(message, strip_signature=strip_signatures)
        for w in msg_warnings:
            data.warnings.append(f"Message {i}: {w}")

        if max_length:
            remaining = max_length - total_length
            if len(body) > remaining:
                if remaining > 100:
                    content_parts.append(body[:remaining])
                content_parts.append(
                    f"\n\n[... TRUNCATED at {max_length:,} chars ...]"
                )
                data.warnings.append(f"Content truncated at {max_length:,} characters")
                truncated = True
                break

        content_parts.append(body)
        content_parts.append("\n")
        total_length += len(body) + 1

        # Attachment/link summary if any
        artifacts_summary = _format_artifacts_summary(message)
        if artifacts_summary:
            content_parts.append(artifacts_summary)
            total_length += len(artifacts_summary)

    # Add truncation notice if we stopped early but didn't already add one
    if max_length and not truncated and i < total_messages:
        content_parts.append(f"\n\n[... TRUNCATED: showing {i} of {total_messages} messages ...]")

    return "".join(content_parts).strip()


def _format_artifacts_summary(message: EmailMessage) -> str:
    """Format attachments and drive links as a summary block."""
    parts: list[str] = []

    # Attachments
    if message.attachments:
        att_lines = ["**Attachments:**"]
        for att in message.attachments:
            size_kb = att.size / 1024
            if size_kb >= 1024:
                size_str = f"{size_kb/1024:.1f} MB"
            else:
                size_str = f"{size_kb:.0f} KB"
            att_lines.append(f"- {att.filename} ({att.mime_type}, {size_str})")
        parts.append("\n".join(att_lines))

    # Drive links (extracted from body)
    if message.drive_links:
        link_lines = ["**Linked files:**"]
        for link in message.drive_links:
            link_lines.append(f"- [{link.get('file_id', 'file')}]({link.get('url', '')})")
        parts.append("\n".join(link_lines))

    if parts:
        return "\n\n" + "\n\n".join(parts) + "\n"
    return ""


# =============================================================================
# PAYLOAD PARSING (for adapter use)
# =============================================================================


def parse_message_payload(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """
    Extract body text from Gmail API message payload.

    This is a utility for adapters - extracts text/plain and text/html
    from the MIME structure.

    Args:
        payload: The 'payload' field from Gmail API message

    Returns:
        Tuple of (plain_text, html) - either may be None
    """
    plain_text = _extract_body_by_mime_type(payload, 'text/plain')
    html = _extract_body_by_mime_type(payload, 'text/html')
    return plain_text, html


def _extract_body_by_mime_type(payload: dict[str, Any], mime_type: str) -> str | None:
    """
    Extract body content by MIME type from message payload.

    Handles both simple and multipart messages recursively.
    """
    # Simple message - check if it matches mime type
    if payload.get('mimeType') == mime_type:
        body_data = payload.get('body', {}).get('data')
        if body_data:
            return base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')

    # Multipart message - search parts
    if 'parts' in payload:
        for part in payload['parts']:
            # Direct match
            if part.get('mimeType') == mime_type:
                body_data = part.get('body', {}).get('data')
                if body_data:
                    return base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')

            # Recurse into nested parts
            if 'parts' in part:
                result = _extract_body_by_mime_type(part, mime_type)
                if result:
                    return result

    return None


def parse_attachments_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract attachment metadata from Gmail API message payload.

    This is a utility for adapters - returns metadata without downloading.

    Args:
        payload: The 'payload' field from Gmail API message

    Returns:
        List of attachment dicts with: filename, mimeType, size, attachment_id
    """
    attachments: list[dict[str, Any]] = []

    def scan_parts(parts: list[dict[str, Any]]) -> None:
        for part in parts:
            # Check if this part is an attachment
            body = part.get('body', {})
            if body.get('attachmentId') and part.get('filename'):
                attachments.append({
                    'filename': part['filename'],
                    'mimeType': part.get('mimeType', 'application/octet-stream'),
                    'size': body.get('size', 0),
                    'attachment_id': body['attachmentId'],
                })
            # Recurse into nested parts
            if 'parts' in part:
                scan_parts(part['parts'])

    if 'parts' in payload:
        scan_parts(payload['parts'])
    elif payload.get('body', {}).get('attachmentId') and payload.get('filename'):
        # Single-part message with attachment
        attachments.append({
            'filename': payload['filename'],
            'mimeType': payload.get('mimeType', 'application/octet-stream'),
            'size': payload['body'].get('size', 0),
            'attachment_id': payload['body']['attachmentId'],
        })

    return attachments
