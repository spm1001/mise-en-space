"""
create_event operation — book a calendar event via do() (mise-rijeco).

Invite-first ergonomics (Sameer's steer, 2026-08-19): the invite IS the
proposal — email ping-pong is the enemy — so the gate makes send-immediately
the smooth path: one preview naming who gets invited plus a clash check
against the user's own diary, then confirm=True books and sends in a single
call. Gate grain is blast radius: attendees present → gated (other people's
diaries and inboxes); no attendees → executes directly, the same judgement
that lets Doc creation run ungated.

Deliberately NOT in remote mode's allowed ops — booking meetings is
organiser-visible mutation.
"""

import logging
from typing import Any

from adapters.calendar import insert_event
from cues_util import with_identity
from models import DoResult, ErrorKind, MiseError
from tools.events_util import (
    REAUTH_ADVICE,
    bound_datetime,
    build_attachments,
    build_event_times,
    byday_mismatch_warning,
    clash_summaries,
    error,
    extract_meet_link,
    meet_request,
    normalise_attendees,
    normalise_recurrence,
    validate_properties,
    validate_send_updates,
)

logger = logging.getLogger(__name__)


def do_create_event(
    title: str | None = None,
    time_min: str | None = None,
    time_max: str | None = None,
    content: str | None = None,
    attendees: list[str] | str | None = None,
    location: str | None = None,
    meet: bool = False,
    recurrence: str | list[str] | None = None,
    include: list[str] | None = None,
    send_updates: str | None = None,
    properties: dict[str, str] | None = None,
    confirm: bool = False,
) -> DoResult | dict[str, Any]:
    """Create an event on the user's primary calendar."""
    assert title is not None and time_min is not None and time_max is not None

    warnings: list[str] = []
    try:
        emails = normalise_attendees(attendees) if attendees else []
        recurrence_lines = normalise_recurrence(recurrence) if recurrence else []
        effective_updates = validate_send_updates(send_updates) or "all"
        programme_keys = validate_properties(properties) if properties else {}
        start, end = build_event_times(
            time_min, time_max, recurring=bool(recurrence_lines),
            warnings=warnings,
        )
    except ValueError as e:
        return error("invalid_input", str(e))

    byday_warning = byday_mismatch_warning(start, recurrence_lines)
    if byday_warning:
        warnings.append(byday_warning)

    # Blast-radius gate: attendees mean other people's diaries and inboxes.
    # The preview carries the clash check so approval is informed; a solo
    # event books directly (own diary, recoverable in the UI).
    if emails and not confirm:
        clashes = clash_summaries(bound_datetime(start), bound_datetime(end))
        preview: dict[str, Any] = {
            "preview": True,
            "operation": "create_event",
            "title": title,
            "start": start,
            "end": end,
            "attendees": emails,
            "send_updates": effective_updates,
            "meet": meet,
            "clashes": clashes,
            "cues": with_identity({
                "confirm_required": (
                    "This is a preview — nothing is booked and nobody has "
                    "been emailed. Show it to the user; to book and send "
                    "invites, call again with confirm=True."
                ),
                "warnings": warnings,
            }),
        }
        if recurrence_lines:
            preview["recurrence"] = recurrence_lines
            preview["clash_note"] = (
                "Clash check covers the FIRST instance only."
            )
        if location:
            preview["location"] = location
        return preview

    body: dict[str, Any] = {"summary": title, "start": start, "end": end}
    if content:
        body["description"] = content
    if location:
        body["location"] = location
    if programme_keys:
        # The adapter adds the mise:minted_by/minted_at stamps on top.
        body["extendedProperties"] = {"private": programme_keys}
    if emails:
        body["attendees"] = [{"email": e} for e in emails]
    if recurrence_lines:
        body["recurrence"] = recurrence_lines
    if meet:
        body["conferenceData"] = meet_request()
    if include:
        try:
            body["attachments"] = build_attachments(include)
        except MiseError as e:
            return error(
                e.kind.value,
                f"Attachment lookup failed before anything was booked: {e.message}",
            )

    try:
        created = insert_event(body, send_updates=effective_updates)
    except MiseError as e:
        if e.kind is ErrorKind.PERMISSION_DENIED:
            return error(e.kind.value, e.message + REAUTH_ADVICE)
        return error(e.kind.value, e.message)

    cues: dict[str, Any] = {"warnings": warnings}
    if emails:
        cues["attendees_invited"] = emails
        cues["attendees_notified"] = (
            f"invites emailed (sendUpdates={effective_updates})"
            if effective_updates != "none"
            else "NO invite emails sent (send_updates='none') — attendees "
                 "see the event only when they look at their calendar"
        )
    meet_link = extract_meet_link(created)
    if meet_link:
        cues["meet_link"] = meet_link
    elif meet:
        warnings.append(
            "meet=True was requested but no Meet link came back — check the "
            "event in the Calendar UI."
        )
    if recurrence_lines:
        cues["recurrence"] = created.get("recurrence", recurrence_lines)
    if include:
        cues["attachments"] = [
            a.get("title") for a in created.get("attachments", [])
        ]
    # Read-back, not echo: proves the stamps + programme keys landed
    # (UI-invisible, so this cue is their only disclosure).
    stamped = created.get("extendedProperties", {}).get("private")
    if stamped:
        cues["properties"] = stamped
    start_tz = start.get("timeZone")
    if start_tz:
        cues["timezone"] = start_tz

    logger.info("create_event: id=%s attendees=%d", created.get("id"), len(emails))
    return DoResult(
        file_id=created.get("id", ""),
        title=created.get("summary", title),
        web_link=created.get("htmlLink", ""),
        operation="create_event",
        cues=cues,
        extras={"type": "calendar_event"},
    )
