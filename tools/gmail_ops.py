"""
Gmail thread operations — archive, label, star via do() verb.

All three are thin wrappers around modify_thread() with label resolution.
Archive = remove INBOX. Star = add STARRED. Label = add/remove by name.

Supports batch: pass a list of thread IDs to process multiple threads in one call.
"""

import logging
from typing import Any

from adapters.gmail import modify_thread, resolve_label_name
from models import DoResult, MiseError
from validation import validate_gmail_id

logger = logging.getLogger(__name__)


def _gmail_thread_link(thread_id: str) -> str:
    """Build Gmail web link for a thread."""
    return f"https://mail.google.com/mail/#all/{thread_id}"


def _batch_result(
    operation: str,
    results: list[dict[str, Any]],
    succeeded: int,
    failed: int,
    **extra: Any,
) -> dict[str, Any]:
    """Build a batch summary dict (shared shape across all Gmail ops)."""
    summary: dict[str, Any] = {
        "operation": operation,
        "batch": True,
        "total": succeeded + failed,
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }
    summary.update(extra)
    return summary


def do_archive(
    file_id: str | list[str] | None = None,
    **_kwargs: Any,
) -> DoResult | dict[str, Any]:
    """
    Archive Gmail thread(s) (remove from Inbox).

    When file_id is a list, archives each thread sequentially and returns
    a batch summary with per-thread results.
    """
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "archive requires 'file_id' (Gmail thread ID)"}

    # Batch path
    if isinstance(file_id, list):
        return _do_batch_archive(file_id)

    # Single path
    try:
        validate_gmail_id(file_id, "file_id")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

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


def _do_batch_archive(thread_ids: list[str]) -> dict[str, Any]:
    """Archive multiple threads, collecting per-thread results."""
    try:
        for i, tid in enumerate(thread_ids):
            validate_gmail_id(tid, f"file_id[{i}]")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    results: list[dict[str, Any]] = []
    succeeded = failed = 0

    for tid in thread_ids:
        try:
            modify_thread(thread_id=tid, remove_label_ids=["INBOX"])
            results.append({"thread_id": tid, "ok": True})
            succeeded += 1
        except MiseError as e:
            results.append({"thread_id": tid, "ok": False, "error": e.message})
            failed += 1

    return _batch_result("archive", results, succeeded, failed)


def do_star(
    file_id: str | list[str] | None = None,
    **_kwargs: Any,
) -> DoResult | dict[str, Any]:
    """
    Star Gmail thread(s).

    When file_id is a list, stars each thread sequentially and returns
    a batch summary with per-thread results.
    """
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "star requires 'file_id' (Gmail thread ID)"}

    # Batch path
    if isinstance(file_id, list):
        return _do_batch_star(file_id)

    # Single path
    try:
        validate_gmail_id(file_id, "file_id")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

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


def _do_batch_star(thread_ids: list[str]) -> dict[str, Any]:
    """Star multiple threads, collecting per-thread results."""
    try:
        for i, tid in enumerate(thread_ids):
            validate_gmail_id(tid, f"file_id[{i}]")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    results: list[dict[str, Any]] = []
    succeeded = failed = 0

    for tid in thread_ids:
        try:
            modify_thread(thread_id=tid, add_label_ids=["STARRED"])
            results.append({"thread_id": tid, "ok": True})
            succeeded += 1
        except MiseError as e:
            results.append({"thread_id": tid, "ok": False, "error": e.message})
            failed += 1

    return _batch_result("star", results, succeeded, failed)


def do_label(
    file_id: str | list[str] | None = None,
    label: str | None = None,
    remove: bool = False,
    **_kwargs: Any,
) -> DoResult | dict[str, Any]:
    """
    Add or remove a label on Gmail thread(s).

    Resolves human-readable label names to Gmail label IDs automatically.
    When file_id is a list, applies the label change to each thread
    sequentially and returns a batch summary with per-thread results.
    """
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "label requires 'file_id' (Gmail thread ID)"}
    if not label:
        return {"error": True, "kind": "invalid_input",
                "message": "label requires 'label' (label name to add/remove)"}

    # Batch path
    if isinstance(file_id, list):
        return _do_batch_label(file_id, label, remove)

    # Single path
    try:
        validate_gmail_id(file_id, "file_id")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

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


def _do_batch_label(
    thread_ids: list[str], label: str, remove: bool,
) -> dict[str, Any]:
    """Apply label change to multiple threads, collecting per-thread results."""
    try:
        for i, tid in enumerate(thread_ids):
            validate_gmail_id(tid, f"file_id[{i}]")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    # Resolve label once for all threads
    try:
        label_id = resolve_label_name(label)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    results: list[dict[str, Any]] = []
    succeeded = failed = 0

    for tid in thread_ids:
        try:
            if remove:
                modify_thread(thread_id=tid, remove_label_ids=[label_id])
            else:
                modify_thread(thread_id=tid, add_label_ids=[label_id])
            results.append({"thread_id": tid, "ok": True})
            succeeded += 1
        except MiseError as e:
            results.append({"thread_id": tid, "ok": False, "error": e.message})
            failed += 1

    return _batch_result(
        "label", results, succeeded, failed,
        label=label, removed=remove,
    )
