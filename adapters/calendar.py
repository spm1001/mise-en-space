"""
Calendar adapter — Google Calendar API v3 wrapper.

Provides event listing with meeting context: attendees, attachments
(Drive file IDs), and Meet links. Primary use case: cross-referencing
Drive files with meetings to explain *why* a document matters.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from adapters.services import get_calendar_service
from models import (
    CalendarAttachment,
    CalendarAttendee,
    CalendarEvent,
    CalendarSearchResult,
)
from retry import with_retry


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


@with_retry(max_attempts=3, delay_ms=1000)
def list_events(
    days_back: int = 7,
    days_forward: int = 7,
    max_results: int = 50,
    page_token: str | None = None,
) -> CalendarSearchResult:
    """
    List calendar events in a time window around now.

    Args:
        days_back: How many days in the past to include.
        days_forward: How many days in the future to include.
        max_results: Maximum events to return (max 2500).
        page_token: Pagination token for next page.

    Returns:
        CalendarSearchResult with events sorted by start time.
    """
    service = get_calendar_service()
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days_back)).isoformat()
    time_max = (now + timedelta(days=days_forward)).isoformat()

    kwargs: dict[str, Any] = {
        "calendarId": "primary",
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": min(max_results, 2500),
    }
    if page_token:
        kwargs["pageToken"] = page_token

    response = service.events().list(**kwargs).execute()

    events = [_parse_event(item) for item in response.get("items", [])]
    warnings: list[str] = []

    return CalendarSearchResult(
        events=events,
        next_page_token=response.get("nextPageToken"),
        warnings=warnings,
    )


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
    service = get_calendar_service()
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days_back)).isoformat()

    kwargs: dict[str, Any] = {
        "calendarId": "primary",
        "timeMin": time_min,
        "timeMax": now.isoformat(),
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": min(max_results, 2500),
    }

    response = service.events().list(**kwargs).execute()

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
