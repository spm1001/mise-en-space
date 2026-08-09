"""
Respond operation — accept/decline/tentative a calendar invite via do() verb.

The one calendar write with a daily use-case (mise-gepiwe): invites arrive in
the inbox mise already triages, has_invite/invite_state already spot and
enrich them; this adds the response verb. Sameer chose the calendar.events
scope over a browser workaround after mise-forunu measured the invite email's
own RSVP links dead (they 302 to a sign-in — the token identifies the
attendee, it does not authorise the write).

file_id takes either id space, routed by shape the way fetch routes Gmail ids:
a 16-hex Gmail id means "the invite thread" (resolved to the event through the
same ICS→iCalUID machinery invite_state uses); anything else is tried as a
Calendar event id directly. Resolution is always disclosed in cues.

Deliberately NOT in remote mode's allowed ops — an RSVP is organiser-visible.
"""

import logging

from adapters.calendar import find_event_by_ical_uid, get_event, respond_to_event
from adapters.gmail import fetch_thread
from extractors.gmail import parse_ics_uid
from models import DoResult, ErrorKind, MiseError
from validation import is_gmail_api_id

logger = logging.getLogger(__name__)

_ACTION_TO_STATUS = {
    "accept": "accepted",
    "decline": "declined",
    "tentative": "tentative",
}

_REAUTH_ADVICE = (
    " If this account authenticated before 2026-08-09 its token predates the "
    "calendar.events scope — run do(operation='setup_oauth', force=True) to "
    "re-authenticate, then retry."
)


def _error(kind: str, message: str) -> dict:
    return {"error": True, "kind": kind, "message": message}


def _resolve_event_from_thread(thread_id: str) -> tuple[dict | None, dict | str]:
    """Resolve an invite thread to its raw calendar event.

    Returns (event, disclosure_cues) on success, (None, error_message) when
    the thread carries no resolvable invite.
    """
    # Import here, not at module top: gmail_attachments sits in the fetch
    # package and pulls the whole eager-extraction stack with it.
    from tools.fetch.gmail_attachments import _download_attachment_bytes

    thread_data = fetch_thread(thread_id)
    # Newest message first: threads.get returns oldest-first, and in a
    # cancel-and-recreate thread the oldest ICS names the DEAD event — the
    # latest invite is the meeting as it now stands.
    for msg in reversed(thread_data.messages):
        if not msg.calendar_attachments:
            continue
        att = msg.calendar_attachments[0]
        raw = _download_attachment_bytes(msg, att, att.mime_type or "text/calendar")
        uid = parse_ics_uid(raw.decode("utf-8", "replace"))
        if not uid:
            continue
        event = find_event_by_ical_uid(uid)
        if event is None:
            return None, (
                f"Thread {thread_id} carries a calendar invite, but no matching "
                "event exists on this account's primary calendar — the invite "
                "was never added (external invites sometimes need opening in "
                "Gmail once). Respond from the calendar UI, or pass the event "
                "id directly if you have it."
            )
        return event, {"resolved_from_thread": thread_id, "ical_uid": uid}

    return None, (
        f"Thread {thread_id} carries no calendar invite, so there is nothing "
        "to respond to. To RSVP a meeting, pass its invite thread or the "
        "Calendar event id."
    )


def do_respond(
    file_id: str | None = None,
    action: str | None = None,
) -> DoResult | dict:
    """Accept, decline, or tentatively respond to a calendar invite."""
    status = _ACTION_TO_STATUS.get(action or "")
    if status is None:
        return _error(
            "invalid_input",
            f"Unknown action '{action}' for respond. "
            "Use one of: accept, decline, tentative.",
        )

    assert file_id is not None  # dispatch REQUIRED_PARAMS guarantees presence
    disclosure: dict = {}
    try:
        if is_gmail_api_id(file_id):
            event, resolved = _resolve_event_from_thread(file_id)
            if event is None:
                return _error("not_found", resolved)  # resolved is the message
            disclosure = resolved  # type: ignore[assignment]
        else:
            event = get_event(file_id)
    except MiseError as e:
        if e.kind is ErrorKind.PERMISSION_DENIED:
            return _error(e.kind.value, e.message + _REAUTH_ADVICE)
        if e.kind is ErrorKind.NOT_FOUND:
            return _error(
                e.kind.value,
                f"'{file_id}' was not found as "
                f"{'a Gmail thread' if is_gmail_api_id(file_id) else 'a calendar event'}. "
                "respond takes the invite's Gmail thread id or the Calendar event id.",
            )
        return _error(e.kind.value, e.message)

    if event.get("status") == "cancelled":
        return _error(
            "invalid_input",
            f"This meeting ('{event.get('summary', 'untitled')}') is CANCELLED — "
            "an RSVP would change nothing for anyone. No response was sent.",
        )

    try:
        respond_to_event(event, status)
    except ValueError as e:
        return _error("invalid_input", str(e))
    except MiseError as e:
        if e.kind is ErrorKind.PERMISSION_DENIED:
            return _error(e.kind.value, e.message + _REAUTH_ADVICE)
        return _error(e.kind.value, e.message)

    start = event.get("start", {})
    cues: dict = {
        "my_response": status,
        "event_summary": event.get("summary"),
        "event_start": start.get("dateTime") or start.get("date"),
        **disclosure,
    }
    logger.info("respond: event=%s action=%s", event.get("id"), action)

    return DoResult(
        file_id=event.get("id", file_id),
        web_link=event.get("htmlLink", ""),
        title=event.get("summary", ""),
        operation="respond",
        cues=cues,
        extras={"type": "calendar_event"},
    )
