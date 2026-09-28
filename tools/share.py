"""
Share operation — share a Drive file with specific people.

Uses Drive API permissions().create() to grant access.
Default role is reader (least privilege).

Two-step confirm gate: first call returns a preview, second call
with confirm=True executes. This prevents Claude from sharing
files without explicit user approval.

Where the connected client declared MCP elicitation (tools/elicit.py),
the same preview text rides a dialog the client renders — do()'s one
confirm resolver (tools/confirm_gate.py) asks with `share_confirm_message`
— and the share executes only on an accepted yes: the human's yes as a UI event rather
than a confirm=True the model supplies itself (mise-jonoha pilot). A
cancelled dialog returns the preview with confirm= still open as the
fallback; a decline closes it.

Non-Google accounts (iCloud, Outlook, etc.) require a notification
email — the API rejects silent sharing. We handle this automatically:
try silent first, fall back to notification if Google requires it.

Uses httpx via MiseSyncClient (Phase 1 migration).
"""

from typing import Any

import httpx

from cues_util import with_identity

from mcp.server.elicitation import ElicitationResult

from tools.elicit import (
    ConfirmAnswer,
    ConfirmVerdict,
    GateQuestion,
    accepted_cue,
    dialog_verdict,
    skipped_preview,
    unaccepted_preview,
)

from adapters.http_client import get_sync_client
from models import DoResult, MiseError
from retry import with_retry
from validation import validate_drive_id

VALID_ROLES = frozenset({"reader", "writer", "commenter"})

# Drive API v3 base URL
_DRIVE_API = "https://www.googleapis.com/drive/v3/files"


def _parse_share_inputs(
    file_id: Any, to: Any, role: str | None,
) -> tuple[str, list[str], str] | dict[str, Any]:
    """Validate share's inputs once, for the body and the confirm resolver.

    Returns (file_id, emails, role) — file_id narrowed to str — or the error
    dict do_share would return.
    """
    if not file_id or not to:
        missing = []
        if not file_id:
            missing.append("file_id")
        if not to:
            missing.append("to (email address)")
        return {"error": True, "kind": "invalid_input",
                "message": f"share requires {' and '.join(missing)}"}

    effective_role = role or "reader"
    if effective_role not in VALID_ROLES:
        return {"error": True, "kind": "invalid_input",
                "message": f"Invalid role '{effective_role}'. Must be one of: {', '.join(sorted(VALID_ROLES))}"}

    # Parse comma-separated emails
    emails = [e.strip() for e in to.split(",") if e.strip()]
    if not emails:
        return {"error": True, "kind": "invalid_input",
                "message": "No valid email addresses in 'to'"}

    try:
        validate_drive_id(file_id, "file_id")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    return file_id, emails, effective_role


def do_share(
    file_id: str | None = None,
    to: str | None = None,
    role: str | None = None,
    confirm: bool = False,
    answer: ElicitationResult[ConfirmAnswer] | None = None,
    question: GateQuestion | None = None,
    **_kwargs: Any,
) -> DoResult | dict[str, Any]:
    """
    Share a file with one or more people via email.

    Two-step operation: call once without confirm to preview,
    then again with confirm=True to execute.

    Args:
        file_id: The file to share
        to: Email address(es), comma-separated for multiple
        role: Permission role — reader (default), writer, or commenter
        confirm: Must be True to actually share. Without it, returns preview.
        answer: The outcome of the client-rendered confirmation dialog
            (tools/confirm_gate.py), or None when no dialog was asked — then
            confirm= is the gate, exactly as before.
        question: What the gate asked, or why it did not; a capable client
            not asked because the preview would not fit gets a cue saying so.

    Returns:
        Preview dict (confirm=False), DoResult (confirm=True), or error dict
    """
    parsed = _parse_share_inputs(file_id, to, role)
    if isinstance(parsed, dict):
        return parsed
    file_id, emails, effective_role = parsed

    verdict = dialog_verdict(answer)
    try:
        if confirm or verdict is None:
            result = _share_file(file_id, emails, effective_role, confirm)
            if not confirm and isinstance(result, dict):
                result = skipped_preview(result, question)
            return result
        return _share_after_dialog(file_id, emails, effective_role, verdict)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


def _share_after_dialog(
    file_id: str, emails: list[str], role: str, verdict: ConfirmVerdict,
) -> DoResult | dict[str, Any]:
    """Act on the client-rendered dialog's answer; share only on an accepted yes.

    The dialog carried exactly the preview text the confirm= path returns
    (share_confirm_message builds it from the same _share_file preview), so
    the human's yes covers the same facts either way. Anything but an accept
    returns that same preview with a confirm_gate cue (tools/elicit.py).
    """
    action, detail = verdict
    if action == "accept":
        result = _share_file(file_id, emails, role, True)
        if isinstance(result, DoResult):
            result.cues["confirm_gate"] = accepted_cue(detail, "shared")
        return result
    preview = _share_file(file_id, emails, role, False)
    if not isinstance(preview, dict):  # confirm=False always previews; keeps mypy honest
        return preview
    return unaccepted_preview(preview, verdict, "Nothing was shared")


def share_confirm_message(file_id: Any, to: Any, role: str | None) -> str | None:
    """The dialog text for an unconfirmed share: the preview's own message.

    Invalid inputs and Drive errors answer None — no dialog — so the body
    then reports them exactly as it does today, instead of the resolver
    failing the call.
    """
    if not isinstance(file_id, str) or not isinstance(to, str):
        return None
    parsed = _parse_share_inputs(file_id, to, role)
    if isinstance(parsed, dict):
        return None
    file_id, emails, effective_role = parsed
    try:
        preview = _share_file(file_id, emails, effective_role, False)
    except MiseError:
        return None
    return preview["message"] if isinstance(preview, dict) else None


@with_retry(max_attempts=3, delay_ms=1000)
def _share_file(
    file_id: str, emails: list[str], role: str, confirm: bool,
) -> DoResult | dict[str, Any]:
    """Preview or execute share via permissions create."""
    client = get_sync_client()

    # Always fetch file metadata — needed for both preview and execute
    file_meta = client.get_json(
        f"{_DRIVE_API}/{file_id}",
        params={"fields": "id,name,webViewLink", "supportsAllDrives": "true"},
    )

    file_name = file_meta.get("name", file_id)
    email_list = ", ".join(emails)

    if not confirm:
        return {
            "preview": True,
            "operation": "share",
            "file_id": file_meta["id"],
            "title": file_name,
            "web_link": file_meta.get("webViewLink", ""),
            "message": f"Would share '{file_name}' with {email_list} as {role}",
            "shared_with": emails,
            "role": role,
            "cues": with_identity({
                "confirm_required": (
                    "This is a preview. To execute, call again with confirm=True. "
                    "Show this to the user and get their approval first."
                ),
            }),
        }

    shared_with = []
    notified = []
    for email in emails:
        _create_permission(client, file_id, email, role, notified)
        shared_with.append(email)

    cues: dict[str, Any] = {
        "action": f"Shared with {', '.join(shared_with)} as {role}",
        "shared_with": shared_with,
        "role": role,
    }
    if notified:
        cues["notified"] = notified
        cues["notification_note"] = (
            "Google required a notification email for non-Google accounts. "
            "These recipients received an invite email from Google."
        )

    return DoResult(
        file_id=file_meta["id"],
        title=file_name,
        web_link=file_meta.get("webViewLink", ""),
        operation="share",
        cues=cues,
    )


def _create_permission(
    client: Any, file_id: str, email: str, role: str, notified: list[str],
) -> None:
    """Create a single permission, falling back to notification for non-Google accounts."""
    body = {"type": "user", "role": role, "emailAddress": email}
    try:
        client.post_json(
            f"{_DRIVE_API}/{file_id}/permissions",
            json_body=body,
            params={"sendNotificationEmail": "false", "supportsAllDrives": "true"},
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400 and "invalidSharingRequest" in e.response.text:
            # Non-Google account — requires notification email
            client.post_json(
                f"{_DRIVE_API}/{file_id}/permissions",
                json_body=body,
                params={"sendNotificationEmail": "true", "supportsAllDrives": "true"},
            )
            notified.append(email)
        else:
            raise
