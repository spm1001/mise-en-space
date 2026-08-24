"""Search result formatters — model → JSON-serializable dict, one per source.

Siblings of tools/search.py (the search_calendar precedent, which has always
held format_calendar_result): pure shaping, no I/O, so the orchestrator stays
under the module-size ceiling. Moved unchanged from search.py (mise-jefaki).
"""

from typing import Any

from adapters.gmail import _is_own_address
from models import CommentActivity, DriveSearchResult, GmailSearchResult
from validation import gmail_thread_web_url


def format_drive_result(result: DriveSearchResult) -> dict[str, Any]:
    """Convert DriveSearchResult to JSON-serializable dict."""
    output: dict[str, Any] = {
        "id": result.file_id,
        "name": result.name,
        "mimeType": result.mime_type,
        "created": result.created_time.isoformat() if result.created_time else None,
        "modified": result.modified_time.isoformat() if result.modified_time else None,
        "url": result.web_view_link,
        "owners": result.owners,
        # Shared Drive files have no owners — the last-modifier is the only
        # honest author signal there (mise-tanoti)
        "last_modified_by": result.last_modified_by,
        "snippet": result.snippet,
    }

    # Add email context for exfil'd files (cross-source linkage)
    if result.email_context:
        output["email_context"] = result.email_context.to_cue()

    return output


def format_gmail_result(result: GmailSearchResult) -> dict[str, Any]:
    """Convert GmailSearchResult to JSON-serializable dict."""
    out = {
        "thread_id": result.thread_id,
        "subject": result.subject,
        "snippet": result.snippet,  # drawn from the LATEST message
        "date": result.date.isoformat() if result.date else None,
        "from": result.from_address,  # thread ORIGINATOR — see last_sender for the latest voice
        "last_sender": result.last_sender,
        "from_me": result.from_me,  # None = identity unresolved, not "someone else"
        "unread_count": result.unread_count,
        "message_count": result.message_count,
        "has_attachments": result.has_attachments,
        "attachment_names": result.attachment_names,
        "is_unread": result.is_unread,
        "labels": result.label_ids,
        "has_invite": result.has_invite,  # thread carries a calendar invite (mise-pinodi)
    }
    # Clickable web URL — only when another party is visibly at an endpoint of
    # the thread (originator or latest sender provably not the user). A thread
    # authored solely by the user may be self-sent (thread-a), whose web token
    # cannot be derived from the API id (mise-lerulo); identity-unresolved
    # threads could be either. Both omit the field rather than risk a link
    # that opens the wrong conversation (mise-hetaba).
    if (_is_own_address(result.from_address) is False
            or _is_own_address(result.last_sender) is False):
        link = gmail_thread_web_url(result.thread_id)
        if link:
            out["web_link"] = link
    return out


def format_activity_result(activity: CommentActivity) -> dict[str, Any]:
    """Convert CommentActivity to JSON-serializable dict for search results."""
    result: dict[str, Any] = {
        "file_id": activity.target.file_id,
        "file_name": activity.target.file_name,
        "mime_type": activity.target.mime_type,
        "url": activity.target.web_link,
        "action_type": activity.action_type,
        "actor": activity.actor.name,
        "timestamp": activity.timestamp,
    }
    if activity.mentioned_users:
        result["mentioned_users"] = activity.mentioned_users
    return result
