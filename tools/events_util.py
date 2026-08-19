"""
Shared machinery for the calendar-write ops (create_event, update_event).

Timezone policy (mise-rijeco): recurring events REQUIRE an IANA zone to
survive DST — a fixed offset turns a 10:00 BST series into 09:00 after the
clock change — and a naive datetime means wall-clock time in the user's
world, not UTC. Both resolve from the user's own diary
(resolve_calendar_timezone); when nothing resolves, the fallback to UTC is
LOUD (warning cue), never silent.
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from adapters.calendar import resolve_calendar_timezone
from adapters.drive import get_file_metadata

_DATE_ONLY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Same advice string family as respond.py, for the older failure (pre-2026-08-09
# tokens lack calendar.events entirely).
REAUTH_ADVICE = (
    " If this account authenticated before 2026-08-09 its token predates the "
    "calendar.events scope — run do(operation='setup_oauth', force=True) to "
    "re-authenticate, then retry."
)

_RECURRENCE_PREFIXES = ("RRULE:", "RDATE", "EXDATE")

_WEEKDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_WEEKDAY_NAMES = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]


def error(kind: str, message: str) -> dict[str, Any]:
    return {"error": True, "kind": kind, "message": message}


def parse_event_time(
    raw: str, param: str, tz: str | None, warnings: list[str],
) -> dict[str, Any]:
    """One event bound → Google start/end dict ({'date':…} or {'dateTime':…}).

    Bare date = all-day (Google's end date is EXCLUSIVE — a one-day event ends
    on the NEXT day's date). Naive datetime gets the resolved zone attached;
    offset-carrying datetimes pass through untouched.

    Raises ValueError with teaching text on unparseable input.
    """
    text = raw.strip()
    if _DATE_ONLY_RE.fullmatch(text):
        return {"date": text}
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"{param} must be an ISO date or datetime — '2026-09-08' or "
            f"'2026-09-08T14:00' or '2026-09-08T14:00:00+01:00' — got {text!r}"
        ) from None
    if dt.tzinfo is None:
        if tz:
            return {"dateTime": text, "timeZone": tz}
        warnings.append(
            f"{param} has no timezone and none could be resolved from your "
            "calendar — treated as UTC. Pass an offset (e.g. +01:00) if that "
            "is wrong."
        )
        return {"dateTime": dt.replace(tzinfo=timezone.utc).isoformat()}
    return {"dateTime": text}


def bound_datetime(time_dict: dict[str, Any]) -> datetime:
    """A start/end dict → aware datetime, for ordering checks and clash windows.

    Naive dateTimes (zone attached separately) and bare dates are pinned to
    UTC — fine for ordering and a clash window, not for display.
    """
    raw = time_dict.get("dateTime") or time_dict["date"]
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def build_event_times(
    time_min: str,
    time_max: str,
    recurring: bool,
    warnings: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Both event bounds, validated as a pair.

    Raises ValueError on: unparseable bound, mixed date/dateTime kinds
    (Google 400s), end not after start, or a recurring event with no
    resolvable zone on a naive time (attached as loud-fallback UTC).
    """
    tz = resolve_calendar_timezone()
    start = parse_event_time(time_min, "time_min", tz, warnings)
    end = parse_event_time(time_max, "time_max", tz, warnings)
    if ("date" in start) != ("date" in end):
        raise ValueError(
            "time_min and time_max must be the same kind — both bare dates "
            "(all-day event) or both datetimes."
        )
    if bound_datetime(end) <= bound_datetime(start):
        raise ValueError(
            f"time_max ({time_max}) must be after time_min ({time_min})."
        )
    if recurring and "dateTime" in start:
        # Google requires start/end timeZone on recurring events; an IANA
        # zone is also what makes the series survive DST.
        zone = tz or "UTC"
        start.setdefault("timeZone", zone)
        end.setdefault("timeZone", zone)
        if not tz:
            warnings.append(
                "Recurring event minted in UTC — no timezone found on your "
                "calendar. Across a DST change the wall-clock time will shift."
            )
    return start, end


def normalise_recurrence(recurrence: str | list[str]) -> list[str]:
    """Recurrence input → Google's recurrence[] lines, validated by prefix.

    Raises ValueError naming the accepted forms on anything else.
    """
    lines = [recurrence] if isinstance(recurrence, str) else list(recurrence)
    cleaned = []
    for line in lines:
        text = line.strip()
        if not text.upper().startswith(_RECURRENCE_PREFIXES):
            raise ValueError(
                f"recurrence lines must be RRULE/RDATE/EXDATE, got {text!r}. "
                "Example: 'RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU' (fortnightly "
                "Tuesdays)."
            )
        cleaned.append(text)
    return cleaned


def byday_mismatch_warning(
    start: dict[str, Any], recurrence: list[str],
) -> str | None:
    """Warn when a weekly BYDAY doesn't include the start's own weekday.

    Probed live 2026-08-19: DTSTART always survives as an instance, so a
    Thursday start with BYDAY=WE yields a stray Thursday meeting plus the
    Wednesday series. Only pure weekday codes are checked — monthly forms
    like BYDAY=2TU are skipped.
    """
    raw = start.get("dateTime") or start.get("date")
    try:
        start_day = datetime.fromisoformat(raw).weekday()
    except ValueError:
        return None
    for line in recurrence:
        upper = line.upper()
        if not upper.startswith("RRULE:") or "FREQ=WEEKLY" not in upper:
            continue
        match = re.search(r"BYDAY=([A-Z,]+)", upper)
        if not match:
            continue
        days = match.group(1).split(",")
        if any(d not in _WEEKDAY_CODES for d in days):
            return None  # numeric-prefixed or malformed — not ours to judge
        if _WEEKDAY_CODES[start_day] not in days:
            return (
                f"The start falls on a {_WEEKDAY_NAMES[start_day]} but "
                f"BYDAY={match.group(1)} — Google keeps the start as an EXTRA "
                "instance outside the pattern. Align time_min with BYDAY "
                "unless that stray first meeting is deliberate."
            )
    return None


def build_attachments(
    file_ids: list[str],
) -> list[dict[str, Any]]:
    """Drive file ids → Calendar attachments[] entries.

    One metadata call per file: it validates the id BEFORE the event books
    (a bad id fails here with a clear NOT_FOUND naming it) and supplies the
    title/mimeType Google would otherwise have to enrich.
    """
    attachments = []
    for fid in file_ids:
        meta = get_file_metadata(fid)
        attachments.append({
            "fileUrl": f"https://drive.google.com/open?id={fid}",
            "title": meta.get("name", fid),
            "mimeType": meta.get("mimeType", ""),
        })
    return attachments


def meet_request() -> dict[str, Any]:
    """A fresh conferenceData.createRequest — Google's Feb 2026 guidance is a
    new Meet code per event, never reused."""
    return {"createRequest": {"requestId": uuid.uuid4().hex}}


def extract_meet_link(event: dict[str, Any]) -> str | None:
    """Meet link from a raw event — same selection as the read adapter."""
    for entry_point in event.get("conferenceData", {}).get("entryPoints", []):
        if entry_point.get("entryPointType") == "video":
            uri: str | None = entry_point.get("uri")
            return uri
    link: str | None = event.get("hangoutLink")
    return link


def normalise_attendees(attendees: list[str] | str) -> list[str]:
    """Attendee input → clean email list. Raises ValueError on a non-email."""
    if isinstance(attendees, str):
        attendees = [a.strip() for a in attendees.split(",")]
    emails = [a.strip() for a in attendees if a and a.strip()]
    for email in emails:
        if "@" not in email:
            raise ValueError(
                f"attendee {email!r} doesn't look like an email address."
            )
    return emails


# The classic event palette (colors.get 'event' map) — fixed and global, so
# enumerable here without the calendars-resource scope mise doesn't hold.
# Labels (eventLabelId) are deliberately NOT surfaced: the palette lives on
# calendars.get (403 on our scopes) and the write accepts an unknown id and
# enriches it to a UUID — probed 2026-08-19, mise-kawegu.
EVENT_COLORS = {
    "1": "lavender", "2": "sage", "3": "grape", "4": "flamingo",
    "5": "banana", "6": "tangerine", "7": "peacock", "8": "graphite",
    "9": "blueberry", "10": "basil", "11": "tomato",
}
_COLOR_BY_NAME = {name: cid for cid, name in EVENT_COLORS.items()}


def validate_color(color: Any) -> str:
    """Colour input (id or name, either case) → colorId string.

    Raises teaching ValueError naming the whole palette — eleven entries is
    small enough to hand the caller the answer inside the refusal.
    """
    text = str(color).strip().lower()
    if text in EVENT_COLORS:
        return text
    if text in _COLOR_BY_NAME:
        return _COLOR_BY_NAME[text]
    palette = ", ".join(f"{cid}={name}" for cid, name in EVENT_COLORS.items())
    raise ValueError(f"color must be one of the event palette: {palette}.")


def validate_properties(properties: Any) -> dict[str, str]:
    """Caller programme keys → a clean extendedProperties.private dict.

    Values are coerced to str (a programme year arriving as an int is not an
    error); keys must be non-empty strings WITHOUT '=' — the equals sign is
    the privateExtendedProperty filter's own key/value separator, so a key
    containing one can never be queried back. Raises teaching ValueError.
    """
    if not isinstance(properties, dict):
        raise ValueError(
            "properties must be an object of key:value pairs, e.g. "
            "{'mise:programme': '1to1-2026'}."
        )
    cleaned: dict[str, str] = {}
    for key, value in properties.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"properties keys must be non-empty strings, got {key!r}.")
        if "=" in key:
            raise ValueError(
                f"properties key {key!r} contains '=' — that is the "
                "privateExtendedProperty filter's separator, so the key could "
                "never be queried. Use ':' or '.' instead."
            )
        cleaned[key.strip()] = str(value)
    return cleaned


def validate_send_updates(send_updates: str | None) -> str | None:
    """Pass through a valid sendUpdates value; raise teaching ValueError else."""
    if send_updates is None:
        return None
    if send_updates not in ("all", "externalOnly", "none"):
        raise ValueError(
            f"send_updates must be all, externalOnly or none — got "
            f"{send_updates!r}."
        )
    return send_updates


def clash_summaries(start_dt: datetime, end_dt: datetime) -> list[str]:
    """The user's own events overlapping [start, end) — the preview's clash
    check. Events the user has DECLINED are excluded (declined slots are
    free in intent). Best-effort: a lookup failure returns a marker string
    rather than blocking the preview."""
    from adapters.calendar import list_events

    try:
        result = list_events(time_min=start_dt, time_max=end_dt, max_results=10)
    except Exception:
        return ["(clash check unavailable — calendar read failed)"]
    clashes = []
    for event in result.events:
        declined = any(
            a.is_self and a.response_status == "declined"
            for a in event.attendees
        )
        if declined:
            continue
        clashes.append(f"{event.summary} ({event.start_time} – {event.end_time})")
    return clashes
