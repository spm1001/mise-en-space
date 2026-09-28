"""
create_event operation — book a calendar event via do() (mise-rijeco).

Invite-first ergonomics (Sameer's steer, 2026-08-19): the invite IS the
proposal — email ping-pong is the enemy — so the gate makes send-immediately
the smooth path: one preview naming who gets invited plus a clash check
against the user's own diary, then confirm=True books and sends in a single
call. Gate grain is blast radius: attendees present → gated (other people's
diaries and inboxes); no attendees → executes directly, the same judgement
that lets Doc creation run ungated.

Where the connected client declared MCP elicitation, the preview's
`message` rides a dialog the client renders (do()'s one confirm resolver,
tools/confirm_gate.py, asks with `create_event_confirm_message`), and the
event books only on an accepted yes (mise-pukiri, after share's pilot in
mise-jonoha). A pre-supplied confirm=True still books without a dialog —
Sameer's policy A, 2026-09-06.

Deliberately NOT in remote mode's allowed ops — booking meetings is
organiser-visible mutation.
"""

import logging
from dataclasses import dataclass
from typing import Any

from mcp.server.elicitation import ElicitationResult

from adapters.calendar import insert_event
from cues_util import current_user_email, with_identity
from models import DoResult, ErrorKind, MiseError
from tools.elicit import ConfirmAnswer, GateQuestion, settle_gate
from tools.events_util import (
    EVENT_COLORS,
    REAUTH_ADVICE,
    build_attachments,
    build_event_times,
    byday_mismatch_warning,
    clash_summaries,
    describe_when,
    error,
    extract_meet_link,
    meet_request,
    normalise_attendees,
    normalise_recurrence,
    readback_field,
    validate_color,
    validate_properties,
    validate_send_updates,
    validate_transparency,
    validate_visibility,
    zoned_datetime,
)

logger = logging.getLogger(__name__)


@dataclass
class _Plan:
    """Validated inputs, shared by the preview, the dialog and the booking."""

    emails: list[str]
    recurrence_lines: list[str]
    effective_updates: str
    programme_keys: dict[str, str]
    color_id: str | None
    vis: str | None
    transp: str | None
    start: dict[str, Any]
    end: dict[str, Any]
    warnings: list[str]


def _plan(
    time_min: str, time_max: str, attendees: list[str] | str | None,
    recurrence: str | list[str] | None, send_updates: str | None,
    properties: dict[str, str] | None, color: str | None,
    visibility: str | None, transparency: str | None,
) -> _Plan | dict[str, Any]:
    """Validate once for every path; an error dict on bad input."""
    warnings: list[str] = []
    try:
        emails = normalise_attendees(attendees) if attendees else []
        recurrence_lines = normalise_recurrence(recurrence) if recurrence else []
        effective_updates = validate_send_updates(send_updates) or "all"
        programme_keys = validate_properties(properties) if properties else {}
        color_id = validate_color(color) if color is not None else None
        vis = validate_visibility(visibility) if visibility is not None else None
        transp = validate_transparency(transparency) if transparency is not None else None
        start, end = build_event_times(
            time_min, time_max, recurring=bool(recurrence_lines),
            warnings=warnings,
        )
    except ValueError as e:
        return error("invalid_input", str(e))

    byday_warning = byday_mismatch_warning(start, recurrence_lines)
    if byday_warning:
        warnings.append(byday_warning)
    return _Plan(emails, recurrence_lines, effective_updates, programme_keys,
                 color_id, vis, transp, start, end, warnings)


_INVITE_EMAILS = {
    "all": "invites emailed",
    "externalOnly": "only external guests emailed",
    "none": "no emails sent",
}


def _preview_message(
    title: str, plan: _Plan, meet: bool, location: str | None,
    include: list[str] | None, clashes: list[str], clash_note: str | None,
) -> str:
    """Everything the preview shows, in words — this IS the dialog's text.

    Short lines, most decisive first: a client dialog shows it only if the
    whole message fits (tools/elicit.py), and otherwise the confirm= path
    shows it in full. The attendee list, the clash check and its caveat, and
    every warning are in it: the human must never approve less than the
    preview shows. Every warning create_event can raise today is longer than
    a dialog line, so a warned booking takes the confirm= path.
    """
    what = f"Book '{title}'"
    if location:
        what += f" at {location}"
    if meet:
        what += " with a Meet link"
    if include:
        what += f" + {len(include)} Drive attachment(s)"
    when = f"When: {describe_when(plan.start, plan.end)}"
    if plan.recurrence_lines:
        when += f"; {'; '.join(plan.recurrence_lines)}"
    clash_line = f"Clashes: {'; '.join(clashes)}." if clashes else "Clashes: none."
    if clash_note:
        clash_line += f" {clash_note}"
    lines = [
        what,
        when,
        f"Invite: {', '.join(plan.emails)} ({_INVITE_EMAILS[plan.effective_updates]})",
        clash_line,
    ]
    lines.extend(f"Warning: {w}" for w in plan.warnings)
    return "\n".join(lines)


def _preview(
    title: str, plan: _Plan, meet: bool, location: str | None,
    include: list[str] | None,
) -> dict[str, Any]:
    """The gated preview: nothing booked, nobody emailed, clash check included."""
    clashes = clash_summaries(zoned_datetime(plan.start), zoned_datetime(plan.end))
    clash_note = (
        "Clash check covers the FIRST instance only."
        if plan.recurrence_lines else None
    )
    preview: dict[str, Any] = {
        "preview": True,
        "operation": "create_event",
        "title": title,
        "start": plan.start,
        "end": plan.end,
        "attendees": plan.emails,
        "send_updates": plan.effective_updates,
        "meet": meet,
        "clashes": clashes,
        "message": _preview_message(
            title, plan, meet, location, include, clashes, clash_note,
        ),
        "cues": with_identity({
            "confirm_required": (
                "This is a preview — nothing is booked and nobody has "
                "been emailed. Show it to the user; to book and send "
                "invites, call again with confirm=True."
            ),
            "warnings": plan.warnings,
        }),
    }
    if plan.recurrence_lines:
        preview["recurrence"] = plan.recurrence_lines
        preview["clash_note"] = clash_note
    if location:
        preview["location"] = location
    return preview


def create_event_confirm_message(
    title: Any, time_min: Any, time_max: Any,
    attendees: list[str] | str | None, location: str | None, meet: bool,
    recurrence: str | list[str] | None, include: list[str] | None,
    send_updates: str | None, properties: dict[str, str] | None,
    color: str | None, visibility: str | None, transparency: str | None,
) -> str | None:
    """The dialog text for an attendee-bearing booking: the preview's message.

    None — no dialog — when there is nothing to gate (no attendees) or the
    inputs would not reach the preview; the body then books solo, or reports
    the error, exactly as it does without a dialog.
    """
    if not (isinstance(title, str) and isinstance(time_min, str)
            and isinstance(time_max, str) and attendees):
        return None
    plan = _plan(time_min, time_max, attendees, recurrence, send_updates,
                 properties, color, visibility, transparency)
    if isinstance(plan, dict) or not plan.emails:
        return None
    return str(_preview(title, plan, meet, location, include)["message"])


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
    color: str | None = None,
    visibility: str | None = None,
    transparency: str | None = None,
    confirm: bool = False,
    answer: ElicitationResult[ConfirmAnswer] | None = None,
    question: GateQuestion | None = None,
) -> DoResult | dict[str, Any]:
    """Create an event on the user's primary calendar.

    `answer` is the client-rendered dialog's outcome and `question` what it
    carried (tools/confirm_gate.py); with no dialog, confirm= is the gate.
    """
    assert title is not None and time_min is not None and time_max is not None

    plan = _plan(time_min, time_max, attendees, recurrence, send_updates,
                 properties, color, visibility, transparency)
    if isinstance(plan, dict):
        return plan
    emails, recurrence_lines = plan.emails, plan.recurrence_lines
    effective_updates, programme_keys = plan.effective_updates, plan.programme_keys
    color_id, vis, transp = plan.color_id, plan.vis, plan.transp
    start, end, warnings = plan.start, plan.end, plan.warnings

    # Blast-radius gate: attendees mean other people's diaries and inboxes.
    # The preview carries the clash check so approval is informed; a solo
    # event books directly (own diary, recoverable in the UI). A dialog the
    # client rendered can stand in for confirm=True — only on an accept of
    # exactly what is about to be booked (settle_gate re-checks the clashes).
    gate_cue = None
    if not confirm:
        stop, gate_cue = settle_gate(
            answer, question, gated=bool(emails),
            preview=lambda: _preview(title, plan, meet, location, include),
            nothing="Nothing was booked and nobody was emailed", done="booked",
        )
        if stop is not None:
            return stop

    body: dict[str, Any] = {"summary": title, "start": start, "end": end}
    if content:
        body["description"] = content
    if location:
        body["location"] = location
    if programme_keys:
        # The adapter adds the mise:minted_by/minted_at stamps on top.
        body["extendedProperties"] = {"private": programme_keys}
    if color_id:
        body["colorId"] = color_id
    if vis:
        body["visibility"] = vis
    if transp:
        body["transparency"] = transp
    if emails:
        body["attendees"] = [{"email": e} for e in emails]
        # The API does NOT add the organiser to attendees the way the Calendar UI
        # does: every 1:1 booked through here listed only the other person, so
        # the invite showed Sameer as organiser but not as a guest (2026-09-23).
        me = current_user_email()
        if me and me.lower() not in {e.lower() for e in emails}:
            body["attendees"].append({"email": me, "responseStatus": "accepted"})
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
    if gate_cue:
        cues["confirm_gate"] = gate_cue
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
    if color_id:
        landed = created.get("colorId")
        cues["color"] = (
            f"{EVENT_COLORS.get(landed, '?')} (colorId {landed})"
            if landed else "requested but ABSENT on read-back"
        )
    if vis:
        cues["visibility"] = readback_field(created, "visibility", vis, "default")
    if transp:
        cues["transparency"] = readback_field(created, "transparency", transp, "opaque")
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
