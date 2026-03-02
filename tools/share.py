"""
Share operation — share a Drive file with specific people.

Uses Drive API permissions().create() to grant access.
Default role is reader (least privilege).

Two-step confirm gate: first call returns a preview, second call
with confirm=True executes. This prevents Claude from sharing
files without explicit user approval.

Non-Google accounts (iCloud, Outlook, etc.) require a notification
email — the API rejects silent sharing. We handle this automatically:
try silent first, fall back to notification if Google requires it.
"""

from typing import Any

from googleapiclient.errors import HttpError

from adapters.services import get_drive_service
from models import DoResult, MiseError
from retry import with_retry

VALID_ROLES = frozenset({"reader", "writer", "commenter"})


def do_share(
    file_id: str | None = None,
    to: str | None = None,
    role: str | None = None,
    confirm: bool = False,
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

    Returns:
        Preview dict (confirm=False), DoResult (confirm=True), or error dict
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
        return _share_file(file_id, emails, effective_role, confirm)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


@with_retry(max_attempts=3, delay_ms=1000)
def _share_file(
    file_id: str, emails: list[str], role: str, confirm: bool,
) -> DoResult | dict[str, Any]:
    """Preview or execute share via permissions().create()."""
    service = get_drive_service()

    # Always fetch file metadata — needed for both preview and execute
    file_meta = (
        service.files()
        .get(fileId=file_id, fields="id,name,webViewLink", supportsAllDrives=True)
        .execute()
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
            "cues": {
                "confirm_required": (
                    "This is a preview. To execute, call again with confirm=True. "
                    "Show this to the user and get their approval first."
                ),
            },
        }

    shared_with = []
    notified = []
    for email in emails:
        _create_permission(service, file_id, email, role, notified)
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
    service: Any, file_id: str, email: str, role: str, notified: list[str],
) -> None:
    """Create a single permission, falling back to notification for non-Google accounts."""
    body = {"type": "user", "role": role, "emailAddress": email}
    try:
        service.permissions().create(
            fileId=file_id, body=body,
            sendNotificationEmail=False, supportsAllDrives=True,
        ).execute()
    except HttpError as e:
        if e.resp.status == 400 and "invalidSharingRequest" in str(e):
            # Non-Google account — requires notification email
            service.permissions().create(
                fileId=file_id, body=body,
                sendNotificationEmail=True, supportsAllDrives=True,
            ).execute()
            notified.append(email)
        else:
            raise
