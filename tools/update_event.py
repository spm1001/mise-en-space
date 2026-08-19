"""
update_event operation — edit a calendar event via do() (mise-rijeco).

Gate grain is blast radius, settled with the design brief's open question:
STRUCTURAL changes (time move, recurrence, attendee add, Meet add) touch
other people's diaries, so they take the share-style confirm gate and email
attendees by default (sendUpdates=all — invite-first). COSMETIC changes
(description, title, location, attachments) execute directly with
sendUpdates=none — the same judgement that lets Doc edits run ungated; the
event's shared copy updates quietly, nobody is emailed, and cues.previous
carries the old values as the undo reference (events have no revision
history, so the prior value IS the restore point).

file_id takes either id space, routed like respond: a 16-hex Gmail id means
"the invite thread"; anything else is tried as a Calendar event id.

Deliberately NOT in remote mode's allowed ops.
"""

import logging
from typing import Any

from adapters.calendar import get_event, patch_event
from cues_util import with_identity
from models import DoResult, ErrorKind, MiseError
from tools.events_util import (
    REAUTH_ADVICE,
    build_attachments,
    build_event_times,
    byday_mismatch_warning,
    error,
    extract_meet_link,
    meet_request,
    EVENT_COLORS,
    normalise_attendees,
    normalise_recurrence,
    validate_color,
    validate_properties,
    validate_send_updates,
)
from tools.respond import _resolve_event_from_thread
from validation import is_gmail_api_id

logger = logging.getLogger(__name__)

_STRUCTURAL = "structural"
_COSMETIC = "cosmetic"


def do_update_event(
    file_id: str | None = None,
    title: str | None = None,
    content: str | None = None,
    location: str | None = None,
    time_min: str | None = None,
    time_max: str | None = None,
    attendees: list[str] | str | None = None,
    recurrence: str | list[str] | None = None,
    include: list[str] | None = None,
    meet: bool = False,
    send_updates: str | None = None,
    properties: dict[str, str] | None = None,
    color: str | None = None,
    confirm: bool = False,
) -> DoResult | dict[str, Any]:
    """Edit an event on the user's primary calendar."""
    assert file_id is not None

    if (time_min is None) != (time_max is None):
        return error(
            "invalid_input",
            "Moving an event needs BOTH time_min and time_max (the new start "
            "and end) — deriving one from the other would be a guess.",
        )

    disclosure: dict[str, Any] = {}
    try:
        if is_gmail_api_id(file_id):
            event, resolved = _resolve_event_from_thread(file_id)
            if event is None:
                return error("not_found", resolved)  # resolved is the message
            disclosure = resolved  # type: ignore[assignment]
        else:
            event = get_event(file_id)
    except MiseError as e:
        if e.kind is ErrorKind.PERMISSION_DENIED:
            return error(e.kind.value, e.message + REAUTH_ADVICE)
        if e.kind is ErrorKind.NOT_FOUND:
            return error(
                e.kind.value,
                f"'{file_id}' was not found as "
                f"{'a Gmail thread' if is_gmail_api_id(file_id) else 'a calendar event'}. "
                "update_event takes the invite's Gmail thread id or the "
                "Calendar event id.",
            )
        return error(e.kind.value, e.message)

    if event.get("status") == "cancelled":
        return error(
            "invalid_input",
            f"'{event.get('summary', 'untitled')}' is CANCELLED — there is "
            "nothing to update.",
        )

    warnings: list[str] = []
    try:
        emails = normalise_attendees(attendees) if attendees else []
        recurrence_lines = normalise_recurrence(recurrence) if recurrence else []
        explicit_updates = validate_send_updates(send_updates)
        programme_keys = validate_properties(properties) if properties else {}
        color_id = validate_color(color) if color is not None else None
    except ValueError as e:
        return error("invalid_input", str(e))

    changes: dict[str, str] = {}  # field -> structural|cosmetic
    if time_min is not None:
        changes["time"] = _STRUCTURAL
    if recurrence_lines:
        changes["recurrence"] = _STRUCTURAL
    if emails:
        changes["attendees"] = _STRUCTURAL
    if meet:
        changes["meet"] = _STRUCTURAL
    if content is not None:
        changes["description"] = _COSMETIC
    if title is not None:
        changes["title"] = _COSMETIC
    if location is not None:
        changes["location"] = _COSMETIC
    if include:
        changes["attachments"] = _COSMETIC
    if programme_keys:
        changes["properties"] = _COSMETIC
    if color_id:
        changes["color"] = _COSMETIC

    if not changes:
        return error(
            "invalid_input",
            "Nothing to change — pass content (description), title, location, "
            "time_min+time_max, attendees (added, never removed), recurrence, "
            "include (Drive attachments), properties (queryable key-values), "
            "color or meet=True.",
        )

    # Guests don't own the event's shape. Attendee-ADD is the one edit Google
    # grants guests by default (guestsCanInviteOthers), so it passes alone;
    # everything else needs the organiser or guestsCanModify.
    organiser = event.get("organizer", {}).get("self", False)
    can_modify = organiser or event.get("guestsCanModify", False)
    if not can_modify:
        only_attendee_add = set(changes) == {"attendees"}
        invites_allowed = event.get("guestsCanInviteOthers", True)
        if not (only_attendee_add and invites_allowed):
            return error(
                "permission_denied",
                f"You are a guest on '{event.get('summary', 'untitled')}' — "
                f"the organiser ({event.get('organizer', {}).get('email', 'unknown')}) "
                "owns its shape. Guests can add attendees when the organiser "
                "allows it; to change your own attendance use "
                "do(operation='respond').",
            )

    structural = [f for f, kind in changes.items() if kind == _STRUCTURAL]
    effective_updates = explicit_updates or ("all" if structural else "none")

    if structural and not confirm:
        preview: dict[str, Any] = {
            "preview": True,
            "operation": "update_event",
            "title": event.get("summary"),
            "event_id": event.get("id"),
            "changes": _describe_changes(
                event, changes, time_min, time_max, emails, recurrence_lines, meet,
            ),
            "send_updates": effective_updates,
            "attendee_count": len(event.get("attendees", [])),
            "cues": with_identity({
                "confirm_required": (
                    "This is a preview — nothing has changed and nobody has "
                    "been emailed. Structural edits (time, recurrence, "
                    "attendees, Meet) touch other people's diaries: show this "
                    "to the user, then call again with confirm=True."
                ),
                **disclosure,
            }),
        }
        return preview

    body: dict[str, Any] = {}
    previous: dict[str, Any] = {}
    if time_min is not None and time_max is not None:
        try:
            start, end = build_event_times(
                time_min, time_max, recurring=bool(
                    recurrence_lines or event.get("recurrence")
                ), warnings=warnings,
            )
        except ValueError as e:
            return error("invalid_input", str(e))
        body["start"], body["end"] = start, end
        previous["start"], previous["end"] = event.get("start"), event.get("end")
    if recurrence_lines:
        byday_warning = byday_mismatch_warning(
            body.get("start", event.get("start", {})), recurrence_lines,
        )
        if byday_warning:
            warnings.append(byday_warning)
        body["recurrence"] = recurrence_lines
        previous["recurrence"] = event.get("recurrence")
    if emails:
        # Patch replaces arrays WHOLESALE — merge, never send the delta.
        existing = list(event.get("attendees", []))
        known = {a.get("email", "").lower() for a in existing}
        added = [e for e in emails if e.lower() not in known]
        if not added:
            warnings.append("All named attendees are already on the event.")
            changes.pop("attendees", None)
        else:
            body["attendees"] = existing + [{"email": e} for e in added]
    if meet:
        if event.get("conferenceData"):
            warnings.append("The event already has a Meet link — left as is.")
            changes.pop("meet", None)
        else:
            body["conferenceData"] = meet_request()
    if content is not None:
        body["description"] = content
        previous["description"] = event.get("description")
    if title is not None:
        body["summary"] = title
        previous["title"] = event.get("summary")
    if location is not None:
        body["location"] = location
        previous["location"] = event.get("location")
    if programme_keys:
        # Patch MERGES the private map per-key (probed 2026-08-19) — send
        # only the new keys; existing ones, including the mint stamps, survive.
        existing_props = event.get("extendedProperties", {}).get("private", {})
        overwritten = {
            k: existing_props[k] for k in programme_keys if k in existing_props
        }
        if overwritten:
            previous["properties"] = overwritten
        body["extendedProperties"] = {"private": programme_keys}
    if color_id:
        body["colorId"] = color_id
        old_color = event.get("colorId")
        previous["color"] = (
            f"{EVENT_COLORS.get(old_color, '?')} (colorId {old_color})"
            if old_color else "calendar default"
        )
    if include:
        try:
            new_attachments = build_attachments(include)
        except MiseError as e:
            return error(
                e.kind.value,
                f"Attachment lookup failed before anything changed: {e.message}",
            )
        existing_atts = list(event.get("attachments", []))
        known_ids = {a.get("fileId") for a in existing_atts}
        known_ids |= {a.get("fileUrl") for a in existing_atts}
        merged = existing_atts + [
            a for a in new_attachments
            if a["fileUrl"] not in known_ids
            and a["fileUrl"].rsplit("id=", 1)[-1] not in known_ids
        ]
        body["attachments"] = merged

    if not body:
        return error(
            "invalid_input",
            "Nothing left to change: " + "; ".join(warnings),
        )

    try:
        patched = patch_event(
            event["id"], body, send_updates=effective_updates,
        )
    except MiseError as e:
        if e.kind is ErrorKind.PERMISSION_DENIED:
            return error(e.kind.value, e.message + REAUTH_ADVICE)
        return error(e.kind.value, e.message)

    cues: dict[str, Any] = {
        "changed": sorted(changes),
        "warnings": warnings,
        **disclosure,
    }
    if previous:
        # Events have no revision history — the old values ARE the restore
        # point. Long descriptions are trimmed; the Calendar UI holds nothing
        # older, so this cue is the only undo reference that exists.
        prev_desc = previous.get("description")
        if isinstance(prev_desc, str) and len(prev_desc) > 1500:
            previous["description"] = prev_desc[:1500] + "… [trimmed]"
        cues["previous"] = previous
    if structural:
        cues["attendees_notified"] = (
            f"attendees emailed about the change (sendUpdates={effective_updates})"
            if effective_updates != "none"
            else "NO update emails sent (send_updates='none')"
        )
    if emails and "attendees" in changes:
        cues["attendees_added"] = added
    if programme_keys:
        # Read-back of the merged map proves the keys landed beside the rest.
        cues["properties"] = patched.get("extendedProperties", {}).get("private", {})
    if color_id:
        landed = patched.get("colorId")
        cues["color"] = (
            f"{EVENT_COLORS.get(landed, '?')} (colorId {landed})"
            if landed else "requested but ABSENT on read-back"
        )
    meet_link = extract_meet_link(patched)
    if meet and meet_link:
        cues["meet_link"] = meet_link

    logger.info(
        "update_event: id=%s changed=%s", event.get("id"), sorted(changes),
    )
    return DoResult(
        file_id=patched.get("id", event.get("id", "")),
        title=patched.get("summary", ""),
        web_link=patched.get("htmlLink", ""),
        operation="update_event",
        cues=cues,
        extras={"type": "calendar_event"},
    )


def _describe_changes(
    event: dict[str, Any],
    changes: dict[str, str],
    time_min: str | None,
    time_max: str | None,
    emails: list[str],
    recurrence_lines: list[str],
    meet: bool,
) -> dict[str, Any]:
    """Old → new, per changed field, for the preview."""
    described: dict[str, Any] = {}
    if "time" in changes:
        described["time"] = {
            "from": {"start": event.get("start"), "end": event.get("end")},
            "to": {"start": time_min, "end": time_max},
        }
    if "recurrence" in changes:
        described["recurrence"] = {
            "from": event.get("recurrence"),
            "to": recurrence_lines,
        }
    if "attendees" in changes:
        existing = {
            a.get("email", "").lower() for a in event.get("attendees", [])
        }
        described["attendees_to_add"] = [
            e for e in emails if e.lower() not in existing
        ]
    if meet and "meet" in changes:
        described["meet"] = "add a Meet link"
    for cosmetic in ("description", "title", "location", "attachments", "properties", "color"):
        if cosmetic in changes:
            described.setdefault("also_cosmetic", []).append(cosmetic)
    return described
