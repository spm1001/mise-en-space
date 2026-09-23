"""
Reply draft operation — create threaded Gmail reply drafts via do() verb.

Fetches the thread, infers recipients from the last message, builds
threading headers, and creates a draft that appears in the correct
Gmail conversation. Draft-only: user reviews and sends from Gmail.
"""

import logging
import re
from email.utils import formataddr, getaddresses
from typing import Any

from adapters.gmail import (
    IncludedLink,
    fetch_thread,
    create_reply_draft,
    delete_draft,
    list_thread_drafts,
    _build_references,
    _ensure_re_prefix,
)
from cues_util import current_user_email
from models import DoResult, EmailMessage, MiseError
from tools.draft import (
    _content_to_html,
    _fetch_signature,
    _format_links_text,
    _format_links_html,
    _resolve_include,
    remember_write,
)
from validation import validate_gmail_id

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


def _parse_cc(cc: str) -> list[tuple[str, str]]:
    """The caller's cc as (name, address) pairs; ValueError if it won't parse.

    getaddresses, not split(","): a display name like "Smith, Jo" carries a
    comma. But on Pythons carrying the CVE-2023-27043 fix, ONE malformed
    element makes getaddresses return [('', '')] for the whole list — so a
    trailing comma once wiped the entire reply-all audience to no Cc at all
    (caught by the essayeur before release). Trailing separators and bare
    semicolons are normalised first; anything still unparseable refuses,
    because merging a half-parsed list is the silent drop this replaced.
    """
    cleaned = cc.strip().strip(",;").strip()
    if '"' not in cleaned:
        cleaned = ", ".join(p.strip() for p in re.split(r"[;,]", cleaned) if p.strip())
    pairs = getaddresses([cleaned]) if cleaned else []
    if not pairs or any("@" not in addr for _, addr in pairs):
        raise ValueError(
            f"cc={cc!r} doesn't parse as an address list — nothing was drafted. "
            "Separate addresses with commas: cc='a@example.com, B <b@example.com>'.")
    return pairs


def _merge_cc(inferred: str | None, explicit: list[tuple[str, str]]) -> str | None:
    """Reply-all's inferred Cc plus the caller's parsed cc, deduped on the address.

    The inferred half is already well-formed (_parse_address_list built each
    entry), so it is kept verbatim and only new explicit addresses append.
    """
    seen = {addr.lower() for _, addr in getaddresses([inferred or ""]) if addr}
    merged = [inferred] if inferred else []
    for name, addr in explicit:
        if addr.lower() not in seen:
            seen.add(addr.lower())
            merged.append(formataddr((name, addr)))
    return ", ".join(merged) or None


def _format_existing_drafts(existing: list[dict[str, str]]) -> str:
    """Render existing-draft details for the guard's teaching error."""
    parts = []
    for d in existing:
        snippet = d.get("snippet", "")[:80]
        parts.append(f"{d['draft_id']} ({snippet!r})")
    return "; ".join(parts)


_NOT_LIVE = {"TRASH", "DRAFT"}


def _reply_anchor(messages: list[EmailMessage]) -> tuple[EmailMessage, int]:
    """The last LIVE message and how many trashed/draft messages sat after it.

    Trashed messages stay in the thread from the API's view, so the tail of a
    thread whose internal asides were binned still read as the one to answer:
    the draft went To: the binned aside's sender, and anchored on a trashed
    message it then vanished from Gmail's conversation view (mise-newidu,
    mise-womuse, 2026-09-02). With no live message at all, the last one stands.
    """
    for back, msg in enumerate(reversed(messages)):
        if not _NOT_LIVE.intersection(msg.label_ids):
            return msg, back
    return messages[-1], 0


def _describe(msg: EmailMessage) -> str:
    when = msg.date.strftime("%Y-%m-%d %H:%MZ") if msg.date else "undated"
    return f"{msg.from_address} ({when})"


def do_reply_draft(
    file_id: str | None = None,
    content: str | None = None,
    cc: str | None = None,
    include: list[str] | None = None,
    reply_all: bool = False,
    supersede: bool = False,
    to: str | None = None,
    **_kwargs: Any,
) -> DoResult | dict[str, Any]:
    """
    Create a threaded reply draft in Gmail.

    Fetches the thread identified by file_id (thread_id), infers recipients
    from the last message, adds threading headers, and creates a draft in
    the correct conversation. Does NOT send.

    Superseded-draft guard (mise-sasivo): Gmail allows N draft objects per
    thread but its conversation view renders only ONE inline — a silently
    created second draft hides exactly where the user would hit Send. So if
    the thread already carries a draft, this refuses with the existing
    draft's id (update it in place via draft(file_id=...), or pass
    supersede=True to discard existing thread drafts and create fresh).

    Args:
        file_id: Gmail thread ID to reply to
        content: Reply body text
        cc: Optional explicit CC override (comma-separated). If not provided
            and reply_all=True, Cc is inferred from the thread.
        include: Optional list of Drive file IDs to include as links
        reply_all: If True, infer Cc from all recipients on the last message
        supersede: If True, discard any existing drafts on this thread before
            creating the new one (drafts.delete is permanent)
        to: Optional explicit To, replacing the inferred sender — for when the
            message being answered is an internal aside (mise-newidu)

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
    try:
        validate_gmail_id(file_id, "file_id")
        explicit_cc = _parse_cc(cc) if reply_all and cc else []
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    # Superseded-draft guard — check before creating, fail open with a
    # warning if the check itself fails (the draft is what was asked for;
    # undisclosed ambiguity, not a listing hiccup, is the trap).
    guard_warnings: list[str] = []
    superseded: list[str] = []
    try:
        existing = list_thread_drafts(file_id)
    except Exception as exc:
        existing = []
        guard_warnings.append(
            f"Could not check for existing drafts on this thread ({exc}) — "
            "if you staged a draft here earlier, prefer updating it via "
            "draft(file_id=<draft_id>)."
        )
    if existing and not supersede:
        details = _format_existing_drafts(existing)
        return {
            "error": True, "kind": "invalid_input",
            "message": (
                f"Thread {file_id} already carries {len(existing)} draft(s): "
                f"{details}. Gmail shows only ONE draft inline per "
                "conversation — a second would be hidden exactly where the "
                "user hits Send. Update the existing draft with "
                "do(operation='draft', file_id='<draft_id>', content=...), "
                "or re-run with supersede=True to discard it and create "
                "fresh (permanent)."
            ),
        }
    if existing and supersede:
        for d in existing:
            try:
                delete_draft(d["draft_id"])
                superseded.append(d["draft_id"])
            except MiseError as e:
                return {"error": True, "kind": e.kind.value,
                        "message": f"supersede failed discarding draft {d['draft_id']}: {e.message}"}

    # Fetch the thread to get threading info and recipients
    try:
        thread = fetch_thread(file_id)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    if not thread.messages:
        return {"error": True, "kind": "invalid_input",
                "message": f"Thread {file_id} has no messages"}

    last_message, skipped = _reply_anchor(thread.messages)
    me = (current_user_email() or "").lower()

    # Infer recipients
    if reply_all:
        inferred_to, inferred_cc = _infer_recipients_all(last_message, current_user_email())
    else:
        inferred_to, inferred_cc = _infer_recipients(last_message)
    to = to or inferred_to

    # Explicit cc ADDS to reply-all's inferred Cc rather than replacing it —
    # replacing silently dropped everyone reply_all had just gathered, and a
    # 2026-03-25 session reported a replied-all draft to the user while its
    # Cc held only the one added name (mise-tijeko: callers expected the
    # union 8/8). Without reply_all there is nothing inferred to keep.
    final_cc = _merge_cc(inferred_cc, explicit_cc) if reply_all else (cc if cc is not None else inferred_cc)

    # Build threading headers
    in_reply_to, references = _build_references(last_message)

    # Subject with Re: prefix
    subject = _ensure_re_prefix(thread.subject)

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

    # The draft's resolved addressing, stated: a fetch of the draft link renders
    # the THREAD, so without this nothing can confirm who it goes to (mise-cesico).
    cues: dict[str, Any] = {
        "action": "Reply draft created \u2014 review and send from Gmail",
        "thread_id": file_id,
        "replying_to": last_message.from_address,
        "reply_anchor": _describe(last_message),
        "to": to,
    }
    warnings: list[str] = []
    if skipped:
        warnings.append(
            f"Skipped {skipped} trashed/draft message(s) at the end of the thread; "
            f"replying to the last live one, from {_describe(last_message)}.")
    originator = thread.messages[0].from_address
    addressed = {_extract_email(a) for _, a in getaddresses([to, final_cc or ""]) if a}
    if _extract_email(originator) not in addressed | {me}:
        warnings.append(
            f"The thread's originator {originator} is not on this draft (To: {to}"
            + (f"; Cc: {final_cc}" if final_cc else "") + "). If the message you are "
            "answering was an internal aside, pass to=/cc= explicitly.")
    if warnings:
        cues.setdefault("warnings", []).extend(warnings)
    if sig_html:
        cues["signature"] = "Gmail signature appended automatically"
    if superseded:
        cues["superseded_drafts"] = superseded
    if guard_warnings:
        cues.setdefault("warnings", []).extend(guard_warnings)
    if final_cc:
        cues["cc"] = final_cc
    if included_links:
        cues["included_links"] = [
            {"title": l.title, "url": l.web_link} for l in included_links
        ]
    if include_warnings:
        cues["include_warnings"] = include_warnings
    if sig_warnings:
        cues["signature_warnings"] = sig_warnings

    remember_write(result.draft_id, result.message_id)
    return DoResult(
        file_id=result.draft_id,
        title=subject,
        web_link=result.web_link,
        operation="reply_draft",
        cues=cues,
    )