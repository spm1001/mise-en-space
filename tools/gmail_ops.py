"""
Gmail thread operations — archive, label, star via do() verb.

All three are thin wrappers around modify_thread() with label resolution.
Archive = remove INBOX. Star = add STARRED. Label = add/remove by name.
"""

import logging
from typing import Any

from adapters.gmail import modify_thread, resolve_label_name
from models import DoResult, MiseError

logger = logging.getLogger(__name__)


def _gmail_thread_link(thread_id: str) -> str:
    """Build Gmail web link for a thread."""
    return f"https://mail.google.com/mail/#all/{thread_id}"


def do_archive(
    file_id: str | None = None,
    **_kwargs: Any,
) -> DoResult | dict[str, Any]:
    """
    Archive a Gmail thread (remove from Inbox).

    Args:
        file_id: Gmail thread ID to archive

    Returns:
        DoResult on success, error dict on failure
    """
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "archive requires 'file_id' (Gmail thread ID)"}

    try:
        result = modify_thread(
            thread_id=file_id,
            remove_label_ids=["INBOX"],
        )
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    return DoResult(
        file_id=result.thread_id,
        title="",
        web_link=_gmail_thread_link(file_id),
        operation="archive",
        cues={"action": "Thread archived — removed from Inbox"},
    )


def do_star(
    file_id: str | None = None,
    **_kwargs: Any,
) -> DoResult | dict[str, Any]:
    """
    Star a Gmail thread.

    Args:
        file_id: Gmail thread ID to star

    Returns:
        DoResult on success, error dict on failure
    """
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "star requires 'file_id' (Gmail thread ID)"}

    try:
        result = modify_thread(
            thread_id=file_id,
            add_label_ids=["STARRED"],
        )
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    return DoResult(
        file_id=result.thread_id,
        title="",
        web_link=_gmail_thread_link(file_id),
        operation="star",
        cues={"action": "Thread starred"},
    )


def do_label(
    file_id: str | None = None,
    label: str | None = None,
    remove: bool = False,
    **_kwargs: Any,
) -> DoResult | dict[str, Any]:
    """
    Add or remove a label on a Gmail thread.

    Resolves human-readable label names to Gmail label IDs automatically.

    Args:
        file_id: Gmail thread ID
        label: Label name (e.g., "Projects/Active", "Follow-up")
        remove: If True, remove the label instead of adding it

    Returns:
        DoResult on success, error dict on failure
    """
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "label requires 'file_id' (Gmail thread ID)"}
    if not label:
        return {"error": True, "kind": "invalid_input",
                "message": "label requires 'label' (label name to add/remove)"}

    # Resolve label name to ID
    try:
        label_id = resolve_label_name(label)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    # Apply the label change
    try:
        if remove:
            result = modify_thread(
                thread_id=file_id,
                remove_label_ids=[label_id],
            )
        else:
            result = modify_thread(
                thread_id=file_id,
                add_label_ids=[label_id],
            )
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    action = f"Label '{label}' {'removed from' if remove else 'added to'} thread"
    return DoResult(
        file_id=result.thread_id,
        title="",
        web_link=_gmail_thread_link(file_id),
        operation="label",
        cues={"action": action, "label": label, "removed": remove},
    )