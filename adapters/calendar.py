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
)
from retry import with_retry


# Google Calendar API v3 base URL
_CALENDAR_API = "https://www.googleapis.com/calendar/v3/calendars"

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
) -> CalendarSearchResult:
    """
    List calendar events in a time window around now, optionally filtered.

    The full window is scanned (paginated internally, bounded at _SCAN_CAP)
    BEFORE the max_results cap is applied, and the cap keeps the events
    nearest to now. Both halves matter: Google returns oldest-first, so a
    single capped page fills up with last week and tomorrow's meeting never
    appears (mise-bidopi — the cap must not eat the future).

    Args:
        days_back: How many days in the past to include.
        days_forward: How many days in the future to include.
        max_results: Maximum events returned; when more match, the ones
            nearest to now win and the result is flagged truncated.
        query: Free-text filter passed as the API's `q` param (matches
            summary, description, attendees, location). Empty = no filter.

    Returns:
        CalendarSearchResult, chronological; .truncated True when events
        were dropped by the cap or the scan bound.
    """
    client = get_sync_client()
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days_back)).isoformat()
    time_max = (now + timedelta(days=days_forward)).isoformat()

    params: dict[str, Any] = {
        "timeMin": time_min,
        "timeMax": time_max,
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
        # Keep the events nearest to now, then restore chronological order.
        events = sorted(events, key=lambda e: abs(_event_start_dt(e) - now))[:max_results]
        events.sort(key=_event_start_dt)

    return CalendarSearchResult(events=events, truncated=truncated)


@with_retry(max_attempts=3, delay_ms=1000)
def find_events_for_file(
    file_id: str,
    days_back: int = 30,
    max_results: int = 100,
) -> CalendarSearchResult:
    """
    Find calendar events that reference a specific Drive file.

    Searches recent events for attachments matching the given file_id.
    Useful for enriching Drive search results with meeting context.

    Args:
        file_id: Drive file ID to search for in event attachments.
        days_back: How far back to search (default 30 days).
        max_results: Max events to scan (default 100).

    Returns:
        CalendarSearchResult with only events that attach this file.
    """
    client = get_sync_client()
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days_back)).isoformat()

    params: dict[str, Any] = {
        "timeMin": time_min,
        "timeMax": now.isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": min(max_results, 2500),
    }

    response = client.get_json(
        f"{_CALENDAR_API}/primary/events",
        params=params,
    )

    matching_events: list[CalendarEvent] = []
    warnings: list[str] = []

    for item in response.get("items", []):
        # Check if any attachment matches the file_id
        for att_data in item.get("attachments", []):
            if att_data.get("fileId") == file_id:
                matching_events.append(_parse_event(item))
                break

    return CalendarSearchResult(
        events=matching_events,
        warnings=warnings,
    )
