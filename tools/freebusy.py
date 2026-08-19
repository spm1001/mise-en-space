"""
freebusy operation — who is free when, answered as data (mise-rijeco).

The op that kills "holding three diaries in working memory": per-person busy
blocks, plus computed common free slots when a duration is given (the slot
mining happens HERE, in code — never by eyeball), plus office days woven in
from workingLocation status events where colleagues' sharing allows.

Honesty invariants, both load-bearing:
- A calendar freebusy CANNOT see is named in not_visible and excluded from
  slot mining with a warning — an invisible diary silently treated as empty
  would present "everyone is free" over a clash.
- Absence of workingLocation events is cued as "location not visible", never
  read as "not in the office" — most sharing is free/busy-only.

Read-only, but needs the calendar.freebusy scope (added 2026-08-19, the one
scope calendar.events does not cover) — a 403 teaches setup_oauth.
"""

import logging
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from adapters.calendar import (
    freebusy_query,
    list_status_events,
    resolve_calendar_timezone,
)
from cues_util import current_user_email, with_identity
from models import ErrorKind, MiseError
from validation import parse_time_window

logger = logging.getLogger(__name__)

# Office-hours fence for slot mining, in the user's own timezone.
_DAY_START = time(9, 0)
_DAY_END = time(17, 30)
_MAX_SLOTS = 20

_FREEBUSY_REAUTH_ADVICE = (
    " free/busy needs the calendar.freebusy scope, added 2026-08-19 — tokens "
    "minted before that date lack it even though every other calendar call "
    "works. Run do(operation='setup_oauth', force=True) to re-authenticate, "
    "then retry."
)


def _error(kind: str, message: str) -> dict[str, Any]:
    return {"error": True, "kind": kind, "message": message}


def do_freebusy(
    attendees: list[str] | str | None = None,
    time_min: str | None = None,
    time_max: str | None = None,
    duration: int | None = None,
) -> dict[str, Any]:
    """Free/busy for a set of people, with optional common-slot mining."""
    assert attendees is not None and time_min is not None and time_max is not None

    if isinstance(attendees, str):
        attendees = [a.strip() for a in attendees.split(",")]
    emails = [a.strip() for a in attendees if a and a.strip()]
    for email in emails:
        if "@" not in email:
            return _error(
                "invalid_input",
                f"attendee {email!r} doesn't look like an email address.",
            )

    try:
        window_min, window_max = parse_time_window(time_min, time_max)
    except ValueError as e:
        return _error("invalid_input", str(e))
    if window_min is None or window_max is None:
        return _error(
            "invalid_input", "freebusy needs both time_min and time_max.",
        )
    if duration is not None and duration <= 0:
        return _error("invalid_input", "duration is minutes and must be > 0.")

    # The user's own diary always joins the arithmetic — a slot that ignores
    # the asker's calendar is not a slot anyone can book.
    me = current_user_email()
    if me and me.lower() not in {e.lower() for e in emails}:
        emails = [me, *emails]

    try:
        calendars = freebusy_query(emails, window_min, window_max)
    except MiseError as e:
        if e.kind in (ErrorKind.PERMISSION_DENIED, ErrorKind.AUTH_EXPIRED):
            return _error(e.kind.value, e.message + _FREEBUSY_REAUTH_ADVICE)
        return _error(e.kind.value, e.message)

    warnings: list[str] = []
    people: dict[str, Any] = {}
    not_visible: list[str] = []
    busy_by_person: dict[str, list[tuple[datetime, datetime]]] = {}

    for email in emails:
        entry = calendars.get(email, {})
        if entry.get("errors"):
            not_visible.append(email)
            continue
        blocks = []
        for block in entry.get("busy", []):
            try:
                blocks.append((
                    datetime.fromisoformat(block["start"]),
                    datetime.fromisoformat(block["end"]),
                ))
            except (KeyError, ValueError):
                continue
        busy_by_person[email] = blocks
        people[email] = {"busy_blocks": len(blocks)}

    if not_visible:
        warnings.append(
            f"Not visible to your account (calendar sharing): "
            f"{', '.join(not_visible)}. They are EXCLUDED from any free-slot "
            "arithmetic below — a slot may clash with their diary."
        )

    # Office days: workingLocation status events, per person, fail-open.
    # ACL-gated by THEIR sharing — free/busy-only sharing raises here while
    # freebusy still answers, and that absence must not read as absence
    # from the office.
    for email in list(people):
        try:
            events = list_status_events(
                email, window_min, window_max, ["workingLocation"],
            )
        except Exception:
            people[email]["office_days"] = (
                "location not visible (their sharing is free/busy-only)"
            )
            continue
        days = {}
        for event in events:
            day = event.get("start", {}).get("date") or (
                event.get("start", {}).get("dateTime", "")[:10]
            )
            props = event.get("workingLocationProperties", {})
            kind = props.get("type", "unknown")
            label = (
                (props.get("officeLocation") or {}).get("label")
                or (props.get("customLocation") or {}).get("label")
                or kind
            )
            if day:
                days[day] = label
        people[email]["office_days"] = days or (
            "no workingLocation events in the window"
        )

    result: dict[str, Any] = {
        "operation": "freebusy",
        "window": {
            "time_min": window_min.isoformat(),
            "time_max": window_max.isoformat(),
        },
        "people": people,
        "busy": {
            email: [
                {"start": s.isoformat(), "end": e.isoformat()}
                for s, e in blocks
            ]
            for email, blocks in busy_by_person.items()
        },
    }
    if not_visible:
        result["not_visible"] = not_visible

    if duration is not None:
        tz_name = resolve_calendar_timezone()
        if not tz_name:
            warnings.append(
                "No timezone found on your calendar — office hours (09:00–"
                "17:30) applied in UTC."
            )
        tz = ZoneInfo(tz_name or "UTC")
        all_busy = [b for blocks in busy_by_person.values() for b in blocks]
        slots = _common_free_slots(
            all_busy, window_min, window_max, duration, tz,
        )
        result["common_free"] = [
            {"start": s.isoformat(), "end": e.isoformat()} for s, e in slots
        ]
        result["slot_note"] = (
            f"Slots are >= {duration} min, weekdays {_DAY_START:%H:%M}–"
            f"{_DAY_END:%H:%M} {tz.key}, across: "
            f"{', '.join(busy_by_person)}."
        )

    result["cues"] = with_identity({"warnings": warnings})
    logger.info(
        "freebusy: people=%d visible=%d duration=%s",
        len(emails), len(busy_by_person), duration,
    )
    return result


def _merge_blocks(
    blocks: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Overlapping/adjacent busy blocks → disjoint sorted blocks."""
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(blocks):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _common_free_slots(
    busy: list[tuple[datetime, datetime]],
    window_min: datetime,
    window_max: datetime,
    duration_minutes: int,
    tz: ZoneInfo,
) -> list[tuple[datetime, datetime]]:
    """Gaps >= duration inside office hours, Mon–Fri, minus all busy blocks."""
    merged = _merge_blocks(busy)
    need = timedelta(minutes=duration_minutes)
    slots: list[tuple[datetime, datetime]] = []

    day = window_min.astimezone(tz).date()
    last_day = window_max.astimezone(tz).date()
    while day <= last_day and len(slots) < _MAX_SLOTS:
        if day.weekday() < 5:  # Mon–Fri
            day_start = max(
                datetime.combine(day, _DAY_START, tzinfo=tz), window_min,
            )
            day_end = min(
                datetime.combine(day, _DAY_END, tzinfo=tz), window_max,
            )
            cursor = day_start
            for busy_start, busy_end in merged:
                if busy_end <= cursor or busy_start >= day_end:
                    continue
                if busy_start - cursor >= need:
                    slots.append((cursor, busy_start))
                cursor = max(cursor, busy_end)
            if day_end - cursor >= need:
                slots.append((cursor, day_end))
        day += timedelta(days=1)
    return slots[:_MAX_SLOTS]
