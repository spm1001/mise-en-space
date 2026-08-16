"""
The calendar leg of search: result formatting, Drive cross-referencing,
and the time-window cues (mise-riduka).

Split from tools/search.py when the explicit time-window params arrived —
that module sits at the 500-line ratchet, and the calendar-specific logic
is the cohesive slice that funds the new capability (the "which unfrozen
sibling owns this logic?" move, .bon/understanding.md).
"""

from collections import defaultdict
from datetime import datetime
from typing import Any

from models import CalendarEvent


def format_calendar_result(event: CalendarEvent) -> dict[str, Any]:
    """Convert CalendarEvent to JSON-serializable dict for search results."""
    human_attendees = [a for a in event.attendees if not a.is_resource]
    result: dict[str, Any] = {
        "event_id": event.event_id,
        "summary": event.summary,
        "start_time": event.start_time,
        "end_time": event.end_time,
        "html_link": event.html_link,
        "organizer": event.organizer_email,
        "attendee_count": len(human_attendees),
        "attendees": [
            {"email": a.email, "name": a.display_name, "status": a.response_status}
            for a in human_attendees[:10]  # Cap for token efficiency
        ],
    }
    if event.attachments:
        result["attachments"] = [
            {"file_id": a.file_id, "title": a.title}
            for a in event.attachments
        ]
        result["attachment_count"] = len(event.attachments)
    if event.meet_link:
        result["meet_link"] = event.meet_link
    return result


def _build_meeting_context_index(
    calendar_events: list[CalendarEvent],
) -> dict[str, list[dict[str, Any]]]:
    """Build file_id → meeting context lookup from calendar events.

    Returns a dict mapping Drive file IDs to lists of meeting context dicts.
    A file may appear in multiple meetings.
    """
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in calendar_events:
        if not event.attachments:
            continue
        human_attendees = [a for a in event.attendees if not a.is_resource]
        context = {
            "summary": event.summary,
            "start_time": event.start_time,
            "attendee_count": len(human_attendees),
            "html_link": event.html_link,
        }
        if event.meet_link:
            context["meet_link"] = event.meet_link
        for att in event.attachments:
            index[att.file_id].append(context)
    return dict(index)


def _enrich_drive_results_with_meetings(
    drive_results: list[dict[str, Any]],
    meeting_index: dict[str, list[dict[str, Any]]],
) -> None:
    """Annotate Drive results with meeting context (mutates in place)."""
    for dr in drive_results:
        file_id = dr.get("id")
        if file_id and file_id in meeting_index:
            dr["meeting_context"] = meeting_index[file_id]


def calendar_window_cue(
    window_min: datetime | None, window_max: datetime | None
) -> str:
    """Disclose the resolved explicit window — date-only bounds are widened
    to whole days in UTC (parse_time_window), so the caller sees what was
    actually asked of the API rather than what they typed."""
    lo = window_min.isoformat() if window_min else "7 days back (default)"
    hi = window_max.isoformat() if window_max else "7 days forward (default)"
    return f"calendar window: {lo} → {hi}"


def calendar_truncated_cue(count: int, explicit_window: bool) -> str:
    """Truncation cue text — the overflow selection differs by window kind
    (adapters/calendar.py::list_events), so the remedy differs too."""
    if explicit_window:
        return (
            f"Results capped at {count} — more events matched in the window; "
            "the chronological head was kept. Raise max_results, or advance "
            "time_min past the last returned event to continue."
        )
    return (
        f"Results capped at {count} — more events matched "
        "in the ±7-day window; the events nearest to now were kept. "
        "Add a query or raise max_results to see more."
    )
