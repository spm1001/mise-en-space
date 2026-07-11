"""
Comment operation — open a NEW comment thread on a Drive file via do().

Creates an unanchored top-level comment (Drive API comments.create). This is
the write-side twin of the comments mise already reads (comments.md on fetch):
it lets a Claude proactively flag something to a human in a doc's comment pane,
not just reply to an existing thread (that's comment_reply). Agent-authored
content is auto-prefixed with '[agent] ' so humans can tell agent comments from
their own — Sameer's convention, matching tools/comment_reply.py.

Anchored comments — tied to a specific text region — are a future extension;
the Drive anchor is a revision-tied region blob, materially harder than
unanchored create.
"""

from typing import Any

from adapters.drive import create_comment
from models import DoResult, MiseError
from validation import validate_drive_id

_AGENT_PREFIX = "[agent] "


def do_comment(
    file_id: str | None = None,
    content: str | None = None,
) -> DoResult | dict[str, Any]:
    """
    Create a new (unanchored) comment on a Drive file.

    Args:
        file_id: The file to comment on
        content: The comment text

    Returns:
        DoResult on success, error dict on failure
    """
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "comment requires 'file_id'"}

    # Normalise whitespace-only content to "absent".
    if content is not None:
        content = content.strip() or None
    if not content:
        return {"error": True, "kind": "invalid_input",
                "message": "comment requires 'content' (the comment text)"}

    try:
        validate_drive_id(file_id, "file_id")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    # Agent self-disclosure: prefix so humans can tell agent comments apart.
    if not content.startswith(_AGENT_PREFIX):
        content = _AGENT_PREFIX + content

    try:
        comment = create_comment(file_id, content)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    return DoResult(
        file_id=file_id,
        title=f"Comment on {file_id}",
        web_link="",
        operation="comment",
        cues={
            "action": f"Created comment {comment.id}",
            "comment_id": comment.id,
        },
    )
