"""
Carry a draft's attachments through an in-place update — mise-mudupa.

drafts.update replaces the message wholesale. Until 2026-09-23 the rebuilt
message carried the draft's To/Subject/Cc and threading headers but NOT its
attachments, so a text improvement to a reply draft silently deleted the
engagement-letter PDF a human had attached in Gmail (2026-08-14; it survived
only because that session had raw-fetched the bytes first).

This module reads the attachments off the stored draft and re-wraps the
rebuilt multipart/alternative body in a multipart/mixed with them. Split from
adapters/gmail.py, which is size-ratcheted.

Inline images (a Content-ID the old HTML body referenced) are NOT carried:
the body is rebuilt from the caller's content, so nothing references them any
more, and re-adding them would surface as stray attachments. They are
reported so the caller can say so.
"""

from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email import encoders
from typing import Any

from adapters.http_client import get_sync_client
from retry import with_retry

_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_ENVELOPE_HEADERS = ("To", "Subject", "Cc", "In-Reply-To", "References")


def _headers(part: dict[str, Any]) -> dict[str, str]:
    return {h.get("name", "").lower(): h.get("value", "") for h in part.get("headers", [])}


def _scan(part: dict[str, Any], found: list[dict[str, Any]]) -> None:
    body = part.get("body", {}) or {}
    if body.get("attachmentId") and part.get("filename"):
        # An attached .eml is carried whole; recursing into it as well would
        # carry its inner attachments a second time (essayeur note).
        hdrs = _headers(part)
        inline = bool(hdrs.get("content-id")) and not hdrs.get(
            "content-disposition", "").lower().startswith("attachment")
        found.append({
            "filename": part["filename"],
            "mimeType": part.get("mimeType", "application/octet-stream"),
            "attachment_id": body["attachmentId"],
            "inline": inline,
        })
        return
    for child in part.get("parts", []) or []:
        _scan(child, found)


@with_retry(max_attempts=3, delay_ms=1000)
def get_draft_attachments(draft_id: str) -> tuple[str, list[dict[str, Any]]]:
    """(message_id, attachment parts) of a stored draft; bodies not downloaded."""
    draft = get_sync_client().get_json(
        f"{_GMAIL_API}/drafts/{draft_id}", params={"format": "full"},
    )
    message = draft.get("message", {}) or {}
    found: list[dict[str, Any]] = []
    _scan(message.get("payload", {}) or {}, found)
    return message.get("id", ""), found


def download_draft_attachments(
    message_id: str, parts: list[dict[str, Any]],
) -> list[tuple[str, str, bytes]]:
    """Download the non-inline parts as (filename, mimeType, bytes)."""
    from adapters.gmail import download_attachment  # local: gmail.py imports this module
    out: list[tuple[str, str, bytes]] = []
    for p in parts:
        if p["inline"]:
            continue
        dl = download_attachment(message_id, p["attachment_id"], p["filename"], p["mimeType"])
        data = dl.temp_path.read_bytes() if dl.temp_path else dl.content
        if dl.temp_path:
            dl.temp_path.unlink(missing_ok=True)
        out.append((p["filename"], p["mimeType"], data))
    return out


def with_attachments(msg: MIMEMultipart, attachments: list[tuple[str, str, bytes]] | None) -> MIMEMultipart:
    """Wrap a multipart/alternative body and its envelope in multipart/mixed."""
    if not attachments:
        return msg
    mixed = MIMEMultipart("mixed")
    for name in _ENVELOPE_HEADERS:
        if name in msg:
            mixed[name] = msg[name]
            del msg[name]
    mixed.attach(msg)
    for filename, mime_type, data in attachments:
        maintype, _, subtype = mime_type.partition("/")
        part = MIMEBase(maintype or "application", subtype or "octet-stream")
        part.set_payload(data)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        mixed.attach(part)
    return mixed
