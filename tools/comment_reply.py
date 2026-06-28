"""
Comment reply operation — reply to (and optionally resolve/reopen) a Drive
file comment via the do() verb.

Reads comment thread IDs from a fetched `comments.md` deposit (each comment's
header carries its id), then posts an in-thread reply. Agent-authored content
is auto-prefixed with '[agent] ' so humans can tell agent replies from their
own (Sameer's convention) — a bare resolve carries no content and so no prefix.
"""

from typing import Any

from adapters.drive import reply_to_comment
from models import DoResult, MiseError
from validation import validate_drive_id

_AGENT_PREFIX = "[agent] "
_VALID_ACTIONS = {"resolve", "reopen"}


def do_comment_reply(
    file_id: str | None = None,
    comment_id: str | None = None,
    content: str | None = None,
    action: str | None = None,
) -> DoResult | dict[str, Any]:
    """
    Reply to a comment on a Drive file, optionally resolving/reopening it.

    A reply needs content, an action ('resolve' | 'reopen'), or both — a bare
    resolve is action='resolve' with no content. Agent-authored content is
    auto-prefixed with '[agent] ' unless already present.

    Args:
        file_id: The file the comment lives on
        comment_id: The comment thread to reply to (from comments.md headers)
        content: Reply text (optional when action resolves/reopens)
        action: 'resolve' or 'reopen' (optional)

    Returns:
        DoResult on success, error dict on failure
    """
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "comment_reply requires 'file_id'"}
    if not comment_id:
        return {"error": True, "kind": "invalid_input",
                "message": "comment_reply requires 'comment_id' (from comments.md)"}

    # Normalise whitespace-only content to "absent" so the need-one check holds.
    if content is not None:
        content = content.strip() or None

    if not content and not action:
        return {"error": True, "kind": "invalid_input",
                "message": "comment_reply requires 'content' and/or 'action' "
                           "(a bare resolve is action='resolve')"}
    if action is not None and action not in _VALID_ACTIONS:
        return {"error": True, "kind": "invalid_input",
                "message": f"action must be one of {sorted(_VALID_ACTIONS)}, got '{action}'"}
    try:
        validate_drive_id(file_id, "file_id")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    # Agent-reply convention: prefix so humans can distinguish agent replies.
    if content and not content.startswith(_AGENT_PREFIX):
        content = _AGENT_PREFIX + content

    try:
        reply = reply_to_comment(file_id, comment_id, content=content, action=action)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    if action == "resolve":
        action_desc = "Replied and resolved" if content else "Resolved"
    elif action == "reopen":
        action_desc = "Replied and reopened" if content else "Reopened"
    else:
        action_desc = "Replied to"

    return DoResult(
        file_id=file_id,
        title=f"Comment {comment_id}",
        web_link="",
        operation="comment_reply",
        cues={
            "action": f"{action_desc} comment {comment_id}",
            "reply_id": reply.id,
            "comment_id": comment_id,
        },
    )
