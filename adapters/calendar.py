"""
Calendar adapter — Google Calendar API v3 wrapper.

Provides event listing with meeting context: attendees, attachments
(Drive file IDs), and Meet links. Primary use case: cross-referencing
Drive files with meetings to explain *why* a document matters.

Uses httpx via MiseSyncClient (Phase 1 migration). Will switch to
MiseHttpClient (async) when the tools/server layer goes async.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from adapters.http_client import get_sync_client
from models import (
    CalendarAttachment,
    CalendarAttendee,
    CalendarEvent,
    CalendarSearchResult,
    InviteState,
)
from retry import with_retry


# Google Calendar API v3 base URL
_CALENDAR_API = "https://www.googleapis.com/calendar/v3/calendars"
# freeBusy is a sibling of /calendars, not under it
_FREEBUSY_API = "https://www.googleapis.com/calendar/v3/freeBusy"

# Internal pagination: events fetched per page while scanning the window
_PAGE_SIZE = 250
# Hard bound on events scanned per list_events call — a ±7 day window rarely
# holds more; the bound only guards against pathological calendars
_SCAN_CAP = 500


def _parse_attendee(data: dict[str, Any]) -> CalendarAttendee:
    """Parse an attendee from Calendar API response."""
    return CalendarAttendee(
        email=data.get("email", ""),
        display_name=data.get("displayName"),
        response_status=data.get("responseStatus", "needsAction"),
        is_self=data.get("self", False),
        is_resource=data.get("resource", False),
    )


def _parse_attachment(data: dict[str, Any]) -> CalendarAttachment | None:
    """Parse an attachment from Calendar API response.

    Returns None if no file_id — only Drive-linked attachments are useful.
    """
    file_id = data.get("fileId")
    if not file_id:
        return None
    return CalendarAttachment(
        file_id=file_id,
        title=data.get("title", ""),
        mime_type=data.get("mimeType"),
        file_url=data.get("fileUrl"),
    )


def _parse_event(data: dict[str, Any]) -> CalendarEvent:
    """Parse a calendar event from Calendar API response."""
    # Start/end can be date (all-day) or dateTime (timed)
    start = data.get("start", {})
    end = data.get("end", {})
    start_time = start.get("dateTime") or start.get("date", "")
    end_time = end.get("dateTime") or end.get("date", "")

    # Attendees — filter out resources for human list, keep resources flagged
    attendees = [
        _parse_attendee(a) for a in data.get("attendees", [])
    ]

    # Attachments — only Drive-linked ones
    attachments = []
    for att_data in data.get("attachments", []):
        att = _parse_attachment(att_data)
        if att:
            attachments.append(att)

    # Meet link from conferenceData or legacy hangoutLink
    meet_link = data.get("hangoutLink")
    conference = data.get("conferenceData", {})
    for entry_point in conference.get("entryPoints", []):
        if entry_point.get("entryPointType") == "video":
            meet_link = entry_point.get("uri")
            break

    # Organizer
    organizer = data.get("organizer", {})

    return CalendarEvent(
        event_id=data.get("id", ""),
        summary=data.get("summary", "(No title)"),
        start_time=start_time,
        end_time=end_time,
        html_link=data.get("htmlLink"),
        attendees=attendees,
        attachments=attachments,
        meet_link=meet_link,
        description=data.get("description"),
        organizer_email=organizer.get("email"),
    )


def _event_start_dt(event: CalendarEvent) -> datetime:
    """Event start as an aware datetime — all-day dates become UTC midnight.

    Unparseable starts sort to the far future so they lose nearest-now
    selection rather than crashing it.
    """
    try:
        dt = datetime.fromisoformat(event.start_time)
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@with_retry(max_attempts=3, delay_ms=1000)
def list_events(
    days_back: int = 7,
    days_forward: int = 7,
    max_results: int = 50,
    query: str = "",
    time_min: datetime | None = None,
    time_max: datetime | None = None,
) -> CalendarSearchResult:
    """
    List calendar events in a time window, optionally filtered.

    The window defaults to ±days_back/days_forward around now; an explicit
    time_min/time_max overrides its bound (either or both — the Calendar API
    takes any absolute range, so historical windows work, mise-riduka).

    The full window is scanned (paginated internally, bounded at _SCAN_CAP)
    BEFORE the max_results cap is applied. Overflow selection depends on the
    window kind: the default now-centred window keeps events nearest to now —
    Google returns oldest-first, so a single capped page fills up with last
    week and tomorrow's meeting never appears (mise-bidopi — the cap must not
    eat the future). An EXPLICIT window keeps chronological order from the
    window start instead: the caller stated their interest, nearest-now would
    bias toward whichever edge is closer to today, and a chronological head
    gives a deterministic cursor (advance time_min past the last event).

    Args:
        days_back: How many days in the past to include (ignored when
            time_min is given).
        days_forward: How many days in the future to include (ignored when
            time_max is given).
        max_results: Maximum events returned; overflow selection as above,
            and the result is flagged truncated.
        query: Free-text filter passed as the API's `q` param (matches
            summary, description, attendees, location). Empty = no filter.
        time_min: Explicit window start (aware datetime).
        time_max: Explicit window end (aware datetime, exclusive).

    Returns:
        CalendarSearchResult, chronological; .truncated True when events
        were dropped by the cap or the scan bound.
    """
    client = get_sync_client()
    now = datetime.now(timezone.utc)
    explicit_window = time_min is not None or time_max is not None
    window_min = time_min or (now - timedelta(days=days_back))
    window_max = time_max or (now + timedelta(days=days_forward))

    params: dict[str, Any] = {
        "timeMin": window_min.isoformat(),
        "timeMax": window_max.isoformat(),
        "singleEvents": "true",  # Google API expects lowercase string
        "orderBy": "startTime",
        "maxResults": _PAGE_SIZE,
    }
    if query.strip():
        params["q"] = query.strip()

    items: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        if page_token:
            params["pageToken"] = page_token
        response = client.get_json(
            f"{_CALENDAR_API}/primary/events",
            params=params,
        )
        items.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token or len(items) >= _SCAN_CAP:
            break

    # Scan bound hit with pages still unread — window not fully seen
    truncated = bool(page_token)

    events = [_parse_event(item) for item in items]

    if len(events) > max_results:
        truncated = True
        if explicit_window:
            # Caller stated the window: keep the chronological head, a
            # deterministic cursor (advance time_min past the last event).
            events.sort(key=_event_start_dt)
            events = events[:max_results]
        else:
            # Keep the events nearest to now, then restore chronological order.
            events = sorted(events, key=lambda e: abs(_event_start_dt(e) - now))[:max_results]
            events.sort(key=_event_start_dt)

    return CalendarSearchResult(events=events, truncated=truncated)


@with_retry(max_attempts=3, delay_ms=1000)
def find_event_by_ical_uid(uid: str) -> dict[str, Any] | None:
    """Resolve an iCalUID to the RAW primary-calendar event, or None.

    `showDeleted=true` is LOAD-BEARING: with the default (false) a cancelled
    event returns ZERO items — invisible, not marked cancelled — so a naive
    lookup concludes "no such event". With it, the cancelled event comes back
    with status=cancelled.

    The raw dict is what respond_to_event needs (id + attendees);
    get_event_by_ical_uid parses the same lookup into an InviteState.
    """
    client = get_sync_client()
    response = client.get_json(
        f"{_CALENDAR_API}/primary/events",
        params={"iCalUID": uid, "showDeleted": "true"},
    )
    items = response.get("items", [])
    return items[0] if items else None


@with_retry(max_attempts=3, delay_ms=1000)
def get_event(event_id: str) -> dict[str, Any]:
    """Fetch one raw event from the primary calendar by event id."""
    client = get_sync_client()
    return client.get_json(f"{_CALENDAR_API}/primary/events/{event_id}")


def respond_to_event(event: dict[str, Any], response_status: str) -> dict[str, Any]:
    """Set the user's own responseStatus on an event they were invited to.

    Read-modify-patch of the FULL attendees array: Calendar patch semantics
    replace array fields WHOLESALE (probed live 2026-08-09, mise-bozumu), so
    sending only the self entry would drop every other attendee from this
    copy. The caller passes the freshly-read event; the whole array goes back
    with one field flipped.

    Raises ValueError when the event has no self attendee — the user's own
    event, or one they were never invited to; RSVP is meaningless there.
    """
    attendees = event.get("attendees", [])
    flipped = False
    for attendee in attendees:
        if attendee.get("self"):
            attendee["responseStatus"] = response_status
            flipped = True
    if not flipped:
        raise ValueError(
            "No self attendee on this event — it is either your own event or "
            "one you were not invited to, so there is no RSVP to set."
        )

    client = get_sync_client()
    return client.patch_json(
        f"{_CALENDAR_API}/primary/events/{event['id']}",
        json_body={"attendees": attendees},
    )


@with_retry(max_attempts=3, delay_ms=1000)
def insert_event(body: dict[str, Any], send_updates: str = "all") -> dict[str, Any]:
    """Create an event on the primary calendar, returning the raw event.

    supportsAttachments rides every insert — harmless without attachments,
    and per Google's reference the API ignores attachments[] without it.
    conferenceDataVersion=1 likewise gates conferenceData.createRequest.
    (Attachment write-through verified live 2026-08-19: read-back showed the
    fileUrl enriched to a resolved fileId. The ignore paths are documented
    behaviour, not probed — both params are simply always sent.)
    """
    client = get_sync_client()
    return client.post_json(
        f"{_CALENDAR_API}/primary/events",
        params={
            "sendUpdates": send_updates,
            "supportsAttachments": "true",
            "conferenceDataVersion": "1",
        },
        json_body=body,
    )


@with_retry(max_attempts=3, delay_ms=1000)
def patch_event(
    event_id: str, body: dict[str, Any], send_updates: str = "none",
) -> dict[str, Any]:
    """Patch an event on the primary calendar, returning the raw event.

    Calendar patch semantics replace array fields WHOLESALE (probed live
    2026-08-09, mise-bozumu) — callers building attendees/attachments bodies
    must send the full merged array, never a delta.
    """
    client = get_sync_client()
    return client.patch_json(
        f"{_CALENDAR_API}/primary/events/{event_id}",
        params={
            "sendUpdates": send_updates,
            "supportsAttachments": "true",
            "conferenceDataVersion": "1",
        },
        json_body=body,
    )


@with_retry(max_attempts=3, delay_ms=1000)
def freebusy_query(
    emails: list[str], time_min: datetime, time_max: datetime,
) -> dict[str, Any]:
    """Free/busy blocks for a set of calendars.

    Returns the raw per-calendar map: email -> {"busy": [...]} or
    {"errors": [...]}. A notFound error means that calendar isn't VISIBLE to
    this account (ACL), not that the person is free — callers must surface
    the difference, or a slot search silently treats an invisible diary as
    an empty one.

    Needs the calendar.freebusy scope (2026-08-19) — calendar.events does not
    cover this endpoint, so pre-existing tokens 403 here while every other
    calendar call works. Callers teach setup_oauth(force=True) on that 403.
    """
    client = get_sync_client()
    response = client.post_json(
        _FREEBUSY_API,
        json_body={
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "items": [{"id": email} for email in emails],
        },
    )
    calendars: dict[str, Any] = response.get("calendars", {})
    return calendars


@with_retry(max_attempts=3, delay_ms=1000)
def list_status_events(
    calendar_id: str,
    time_min: datetime,
    time_max: datetime,
    event_types: list[str],
) -> list[dict[str, Any]]:
    """Status events (workingLocation/outOfOffice/focusTime) from a calendar.

    calendar_id may be a colleague's email — visibility is gated by THEIR
    calendar sharing (ACL), not by scope: free/busy-only sharing raises
    NOT_FOUND here while freebusy_query still answers. Callers cue that
    honestly rather than reading it as "no office days".
    """
    client = get_sync_client()
    response = client.get_json(
        f"{_CALENDAR_API}/{calendar_id}/events",
        params={
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": _PAGE_SIZE,
            "eventTypes": event_types,
        },
    )
    items: list[dict[str, Any]] = response.get("items", [])
    return items


# Process-lifetime cache: the user's timezone doesn't change mid-session
_TZ_CACHE: list[str | None] = []


def resolve_calendar_timezone() -> str | None:
    """The user's IANA timezone, read from their own diary.

    calendars.get('primary') needs a scope mise doesn't hold (probed 403,
    2026-08-19), but UI-created events carry start.timeZone — the diary
    itself is the source. Recurring events REQUIRE an IANA zone to survive
    DST (a fixed offset turns a 10:00 BST series into 09:00 after the clock
    change), which is why callers resolve this rather than passing offsets.

    Returns None when no recent event carries a zone; callers fall back to
    UTC with a warning rather than guessing.
    """
    if _TZ_CACHE:
        return _TZ_CACHE[0]
    client = get_sync_client()
    now = datetime.now(timezone.utc)
    try:
        response = client.get_json(
            f"{_CALENDAR_API}/primary/events",
            params={
                "timeMin": (now - timedelta(days=60)).isoformat(),
                "timeMax": (now + timedelta(days=60)).isoformat(),
                "maxResults": 50,
                "fields": "items(start(timeZone),organizer(self))",
            },
        )
    except Exception:
        return None  # best-effort — never fail the write over a tz lookup
    items = response.get("items", [])
    zones = [i.get("start", {}).get("timeZone") for i in items]
    self_zones = [
        z for i, z in zip(items, zones)
        if z and i.get("organizer", {}).get("self")
    ]
    resolved = self_zones[0] if self_zones else next((z for z in zones if z), None)
    _TZ_CACHE.append(resolved)
    return resolved


def get_event_by_ical_uid(uid: str) -> InviteState | None:
    """Resolve an invitation's iCalUID to its LIVE calendar event state.

    An invite email's ICS is a frozen snapshot; this reads the current state
    from the user's primary calendar so a cancelled/rescheduled meeting is
    disclosed rather than repeated stale (mise-pinodi / meduto exploration).

    Returns None when no matching event exists on the primary calendar (e.g.
    an external invite never added to this calendar). Raises on API error —
    the caller decides whether to skip (guest mode may lack calendar scope).
    """
    event = find_event_by_ical_uid(uid)
    if event is None:
        return None

    status = event.get("status", "confirmed")

    my_response: str | None = None
    for attendee in event.get("attendees", []):
        if attendee.get("self"):
            my_response = attendee.get("responseStatus")
            break

    start = event.get("start", {})
    current_start = start.get("dateTime") or start.get("date")

    # 'updated' is the last-modified time; for a cancelled event that is the
    # cancellation time. Only surface it when actually cancelled.
    cancelled_at = event.get("updated") if status == "cancelled" else None

    return InviteState(
        status=status,
        my_response=my_response,
        current_start=current_start,
        cancelled_at=cancelled_at,
    )
