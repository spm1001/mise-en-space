"""
Integration tests for Calendar API adapter.

Run with: uv run pytest tests/integration/test_calendar_adapter.py -v -m integration
"""

import pytest

from adapters.calendar import list_events, find_events_for_file
from models import CalendarSearchResult, CalendarEvent, CalendarAttendee


# ============================================================================
# list_events
# ============================================================================


@pytest.mark.integration
def test_list_events_returns_result() -> None:
    """list_events returns a CalendarSearchResult."""
    result = list_events(days_back=7, days_forward=7, max_results=5)

    assert isinstance(result, CalendarSearchResult)
    assert isinstance(result.events, list)
    assert isinstance(result.warnings, list)


@pytest.mark.integration
def test_list_events_have_structure() -> None:
    """Each event has required fields populated."""
    result = list_events(days_back=7, days_forward=7, max_results=10)

    for event in result.events:
        assert isinstance(event, CalendarEvent)
        assert event.event_id  # Non-empty
        assert event.summary  # Non-empty (placeholder if missing)
        assert event.start_time  # Non-empty
        assert event.end_time  # Non-empty


@pytest.mark.integration
def test_list_events_max_results() -> None:
    """max_results limits the number of events returned."""
    result = list_events(days_back=30, days_forward=30, max_results=3)
    assert len(result.events) <= 3


@pytest.mark.integration
def test_list_events_attendees_are_typed() -> None:
    """Attendees on events are CalendarAttendee instances."""
    result = list_events(days_back=7, days_forward=7, max_results=20)

    events_with_attendees = [e for e in result.events if e.attendees]
    if not events_with_attendees:
        pytest.skip("No events with attendees in the test window")

    for event in events_with_attendees:
        for att in event.attendees:
            assert isinstance(att, CalendarAttendee)
            assert att.email  # Non-empty


# ============================================================================
# find_events_for_file
# ============================================================================


@pytest.mark.integration
def test_find_events_for_file_returns_result() -> None:
    """find_events_for_file returns a CalendarSearchResult even with no matches."""
    # Use a fake file ID — should return empty but not error
    result = find_events_for_file("nonexistent_file_id_xyz", days_back=7)

    assert isinstance(result, CalendarSearchResult)
    assert result.events == []
