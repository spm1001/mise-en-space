"""
Share operation — share a Drive file with specific people.

Uses Drive API permissions().create() to grant access.
Default role is reader (least privilege).

Two-step confirm gate: first call returns a preview, second call
with confirm=True executes. This prevents Claude from sharing
files without explicit user approval.

Where the connected client declared MCP elicitation (tools/elicit.py),
the same preview text rides a dialog the client renders — via the
`share_confirm` resolver on do()'s `share_answer` param — and the share
executes only on an accepted yes: the human's yes as a UI event rather
than a confirm=True the model supplies itself (mise-jonoha pilot). A
cancelled dialog returns the preview with confirm= still open as the
fallback; a decline closes it.

Non-Google accounts (iCloud, Outlook, etc.) require a notification
email — the API rejects silent sharing. We handle this automatically:
try silent first, fall back to notification if Google requires it.

Uses httpx via MiseSyncClient (Phase 1 migration).
"""

from typing import Annotated, Any

import httpx

from cues_util import with_identity

from mcp.server.elicitation import ElicitationResult
from mcp.server.mcpserver import Context, Elicit, Resolve

from tools.elicit import ConfirmAnswer, ConfirmVerdict, confirm_marker, dialog_verdict

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
        answer: The outcome of the client-rendered confirmation dialog (the
            `share_confirm` resolver), or None when no dialog was asked — then
            confirm= is the gate, exactly as before.

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
            return _share_file(file_id, emails, effective_role, confirm)
        return _share_after_dialog(file_id, emails, effective_role, verdict)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


def _share_after_dialog(
    file_id: str, emails: list[str], role: str, verdict: ConfirmVerdict,
) -> DoResult | dict[str, Any]:
    """Act on the client-rendered dialog's answer; share only on an accepted yes.

    The dialog carried exactly the preview text the confirm= path returns
    (share_confirm builds it from the same _share_file preview), so the
    human's yes covers the same facts either way. Anything but an accept
    returns that same preview, with a cue saying what the dialog did and
    whether the confirm= round-trip is still open:

    - cancel: no dialog decided this (headless clients auto-cancel; a
      dismissed dialog cancels) — confirm= stays the fallback.
    - decline: an answer. The confirm_required cue is withdrawn so the model
      is not nudged into supplying the yes the dialog just refused.

    The cue names the mechanism and the client's answer — never "the human
    approved": from here an auto-resolved accept is indistinguishable.
    """
    action, detail = verdict
    if action == "accept":
        result = _share_file(file_id, emails, role, True)
        if isinstance(result, DoResult):
            result.cues["confirm_gate"] = f"elicitation: {detail}; shared on that answer"
        return result
    preview = _share_file(file_id, emails, role, False)
    if not isinstance(preview, dict):  # confirm=False always previews; keeps mypy honest
        return preview
    cues = dict(preview["cues"])
    if action == "decline":
        cues.pop("confirm_required", None)
        cues["confirm_gate"] = (
            f"elicitation: declined — {detail}. Nothing was shared. The user said no "
            "through the dialog; ask them again before any further attempt."
        )
    else:
        cues["confirm_gate"] = (
            f"elicitation: {action} — {detail}. Nothing was shared; no dialog decided "
            "this, so the confirm= round-trip applies: show this preview to the user "
            "and call again with confirm=True on their yes."
        )
    return {**preview, "cues": cues}


def share_confirm(
    operation: str, ctx: Context, file_id: Any = None, to: Any = None,
    role: str | None = None, confirm: bool = False,
) -> Elicit[ConfirmAnswer] | None:
    """Resolver for do()'s `share_answer`: the confirmation question, or None.

    Runs before the tool body on EVERY do() call, so it answers None fast for
    anything that is not an unconfirmed share on a dialog-capable client.
    Invalid inputs and Drive errors also answer None: the body then reports
    them exactly as it does today, instead of the resolver failing the call.
    """
    if operation != "share" or confirm or not isinstance(file_id, str) or not isinstance(to, str):
        return None
    parsed = _parse_share_inputs(file_id, to, role)
    if isinstance(parsed, dict):
        return None
    file_id, emails, effective_role = parsed
    try:
        preview = _share_file(file_id, emails, effective_role, False)
    except MiseError:
        return None
    message = preview["message"] if isinstance(preview, dict) else None
    return confirm_marker(ctx, message)


# do()'s injected param (server.py): mcp fills it by running share_confirm and
# asking the client; the union annotation hands the body accept/decline/cancel
# rather than aborting the call on a no. Never a wire param.
ShareAnswer = Annotated[ElicitationResult[ConfirmAnswer], Resolve(share_confirm)]


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
