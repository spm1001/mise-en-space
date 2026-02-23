"""
Tests for Calendar API adapter and models.
"""

from unittest.mock import patch, MagicMock

from tests.helpers import mock_api_chain

from models import (
    CalendarAttachment,
    CalendarAttendee,
    CalendarEvent,
    CalendarSearchResult,
)
from adapters.calendar import (
    _parse_attendee,
    _parse_attachment,
    _parse_event,
    list_events,
    find_events_for_file,
)


# ============================================================================
# MODELS
# ============================================================================


class TestCalendarModels:
    """Tests for Calendar data models."""

    def test_attachment_defaults(self) -> None:
        att = CalendarAttachment(file_id="f1", title="Doc")
        assert att.file_id == "f1"
        assert att.title == "Doc"
        assert att.mime_type is None
        assert att.file_url is None

    def test_attendee_defaults(self) -> None:
        att = CalendarAttendee(email="alice@example.com")
        assert att.email == "alice@example.com"
        assert att.display_name is None
        assert att.response_status == "needsAction"
        assert att.is_self is False
        assert att.is_resource is False

    def test_event_defaults(self) -> None:
        event = CalendarEvent(
            event_id="e1",
            summary="Standup",
            start_time="2026-02-23T09:00:00Z",
            end_time="2026-02-23T09:30:00Z",
        )
        assert event.event_id == "e1"
        assert event.attendees == []
        assert event.attachments == []
        assert event.meet_link is None
        assert event.description is None
        assert event.organizer_email is None

    def test_search_result_defaults(self) -> None:
        result = CalendarSearchResult(events=[])
        assert result.events == []
        assert result.next_page_token is None
        assert result.warnings == []


# ============================================================================
# PURE PARSERS
# ============================================================================


class TestParseAttendee:
    """Test _parse_attendee with various structures."""

    def test_basic(self) -> None:
        att = _parse_attendee({
            "email": "alice@example.com",
            "displayName": "Alice",
            "responseStatus": "accepted",
        })
        assert att.email == "alice@example.com"
        assert att.display_name == "Alice"
        assert att.response_status == "accepted"
        assert att.is_self is False

    def test_self_flag(self) -> None:
        att = _parse_attendee({"email": "me@example.com", "self": True})
        assert att.is_self is True

    def test_resource_flag(self) -> None:
        att = _parse_attendee({
            "email": "room@resource.calendar.google.com",
            "resource": True,
        })
        assert att.is_resource is True

    def test_empty_data(self) -> None:
        att = _parse_attendee({})
        assert att.email == ""
        assert att.response_status == "needsAction"


class TestParseAttachment:
    """Test _parse_attachment with various structures."""

    def test_drive_file(self) -> None:
        att = _parse_attachment({
            "fileId": "abc123",
            "title": "Meeting Notes",
            "mimeType": "application/vnd.google-apps.document",
            "fileUrl": "https://drive.google.com/open?id=abc123",
        })
        assert att is not None
        assert att.file_id == "abc123"
        assert att.title == "Meeting Notes"
        assert att.mime_type == "application/vnd.google-apps.document"

    def test_no_file_id_returns_none(self) -> None:
        """Non-Drive attachments (no fileId) are skipped."""
        assert _parse_attachment({"title": "Some link"}) is None

    def test_empty_data(self) -> None:
        assert _parse_attachment({}) is None


class TestParseEvent:
    """Test _parse_event with various event structures."""

    def test_timed_event(self) -> None:
        event = _parse_event({
            "id": "e1",
            "summary": "1:1 with Bob",
            "start": {"dateTime": "2026-02-23T14:00:00Z"},
            "end": {"dateTime": "2026-02-23T14:30:00Z"},
            "htmlLink": "https://calendar.google.com/event?eid=e1",
        })
        assert event.event_id == "e1"
        assert event.summary == "1:1 with Bob"
        assert event.start_time == "2026-02-23T14:00:00Z"
        assert event.end_time == "2026-02-23T14:30:00Z"
        assert event.html_link == "https://calendar.google.com/event?eid=e1"

    def test_all_day_event(self) -> None:
        """All-day events use 'date' not 'dateTime'."""
        event = _parse_event({
            "id": "e2",
            "summary": "Holiday",
            "start": {"date": "2026-02-23"},
            "end": {"date": "2026-02-24"},
        })
        assert event.start_time == "2026-02-23"
        assert event.end_time == "2026-02-24"

    def test_no_title(self) -> None:
        """Missing summary gets placeholder."""
        event = _parse_event({"id": "e3", "start": {}, "end": {}})
        assert event.summary == "(No title)"

    def test_with_attendees(self) -> None:
        event = _parse_event({
            "id": "e4",
            "summary": "Team sync",
            "start": {"dateTime": "2026-02-23T10:00:00Z"},
            "end": {"dateTime": "2026-02-23T10:30:00Z"},
            "attendees": [
                {"email": "alice@example.com", "responseStatus": "accepted"},
                {"email": "bob@example.com", "responseStatus": "tentative"},
            ],
        })
        assert len(event.attendees) == 2
        assert event.attendees[0].email == "alice@example.com"
        assert event.attendees[1].response_status == "tentative"

    def test_with_attachments(self) -> None:
        event = _parse_event({
            "id": "e5",
            "summary": "Review",
            "start": {"dateTime": "2026-02-23T11:00:00Z"},
            "end": {"dateTime": "2026-02-23T12:00:00Z"},
            "attachments": [
                {"fileId": "doc1", "title": "Agenda", "mimeType": "application/vnd.google-apps.document"},
                {"title": "No file ID link"},  # Skipped
            ],
        })
        assert len(event.attachments) == 1
        assert event.attachments[0].file_id == "doc1"

    def test_meet_link_from_conference_data(self) -> None:
        """conferenceData.entryPoints video URI takes priority."""
        event = _parse_event({
            "id": "e6",
            "summary": "Call",
            "start": {"dateTime": "2026-02-23T15:00:00Z"},
            "end": {"dateTime": "2026-02-23T16:00:00Z"},
            "hangoutLink": "https://meet.google.com/old-link",
            "conferenceData": {
                "entryPoints": [
                    {"entryPointType": "phone", "uri": "tel:+1234"},
                    {"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"},
                ],
            },
        })
        assert event.meet_link == "https://meet.google.com/abc-defg-hij"

    def test_meet_link_fallback_to_hangout(self) -> None:
        """Falls back to hangoutLink when no conferenceData."""
        event = _parse_event({
            "id": "e7",
            "summary": "Call",
            "start": {"dateTime": "2026-02-23T15:00:00Z"},
            "end": {"dateTime": "2026-02-23T16:00:00Z"},
            "hangoutLink": "https://meet.google.com/legacy",
        })
        assert event.meet_link == "https://meet.google.com/legacy"

    def test_organizer(self) -> None:
        event = _parse_event({
            "id": "e8",
            "summary": "Planning",
            "start": {"dateTime": "2026-02-23T09:00:00Z"},
            "end": {"dateTime": "2026-02-23T10:00:00Z"},
            "organizer": {"email": "boss@example.com"},
        })
        assert event.organizer_email == "boss@example.com"

    def test_empty_event(self) -> None:
        """Minimal event data doesn't crash."""
        event = _parse_event({})
        assert event.event_id == ""
        assert event.summary == "(No title)"
        assert event.start_time == ""
        assert event.end_time == ""


# ============================================================================
# list_events (mocked service)
# ============================================================================


def _api_event(
    *,
    event_id: str = "evt1",
    summary: str = "Test Event",
    start: str = "2026-02-23T10:00:00Z",
    end: str = "2026-02-23T11:00:00Z",
    attachments: list[dict] | None = None,
    attendees: list[dict] | None = None,
) -> dict:
    """Build an API-shaped event dict."""
    event: dict = {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }
    if attachments:
        event["attachments"] = attachments
    if attendees:
        event["attendees"] = attendees
    return event


class TestListEvents:
    """Test list_events with mocked Calendar API."""

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_calendar_service")
    def test_basic_list(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "events.list.execute", {
            "items": [_api_event()],
        })

        result = list_events()

        assert isinstance(result, CalendarSearchResult)
        assert len(result.events) == 1
        assert result.events[0].summary == "Test Event"

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_calendar_service")
    def test_empty_response(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "events.list.execute", {})

        result = list_events()

        assert result.events == []
        assert result.next_page_token is None

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_calendar_service")
    def test_pagination_token(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "events.list.execute", {
            "items": [_api_event()],
            "nextPageToken": "page2",
        })

        result = list_events()

        assert result.next_page_token == "page2"

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_calendar_service")
    def test_page_token_forwarded(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "events.list.execute", {"items": []})

        list_events(page_token="tok123")

        call_kwargs = mock_service.events().list.call_args[1]
        assert call_kwargs["pageToken"] == "tok123"

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_calendar_service")
    def test_max_results_capped(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "events.list.execute", {"items": []})

        list_events(max_results=5000)

        call_kwargs = mock_service.events().list.call_args[1]
        assert call_kwargs["maxResults"] == 2500

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_calendar_service")
    def test_single_events_and_order(self, mock_svc, _sleep) -> None:
        """Verifies singleEvents=True and orderBy=startTime are set."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "events.list.execute", {"items": []})

        list_events()

        call_kwargs = mock_service.events().list.call_args[1]
        assert call_kwargs["singleEvents"] is True
        assert call_kwargs["orderBy"] == "startTime"
        assert call_kwargs["calendarId"] == "primary"

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_calendar_service")
    def test_multiple_events(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "events.list.execute", {
            "items": [
                _api_event(event_id="e1", summary="Morning standup"),
                _api_event(event_id="e2", summary="Lunch"),
                _api_event(event_id="e3", summary="Review"),
            ],
        })

        result = list_events()

        assert len(result.events) == 3
        summaries = [e.summary for e in result.events]
        assert summaries == ["Morning standup", "Lunch", "Review"]


# ============================================================================
# find_events_for_file (mocked service)
# ============================================================================


class TestFindEventsForFile:
    """Test find_events_for_file with mocked Calendar API."""

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_calendar_service")
    def test_finds_matching_event(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "events.list.execute", {
            "items": [
                _api_event(
                    event_id="e1",
                    summary="Review meeting",
                    attachments=[{"fileId": "target_file", "title": "Agenda"}],
                ),
                _api_event(event_id="e2", summary="Unrelated"),
            ],
        })

        result = find_events_for_file("target_file")

        assert len(result.events) == 1
        assert result.events[0].event_id == "e1"
        assert result.events[0].summary == "Review meeting"

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_calendar_service")
    def test_no_match(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "events.list.execute", {
            "items": [
                _api_event(event_id="e1", summary="No attachments"),
                _api_event(
                    event_id="e2",
                    attachments=[{"fileId": "other_file", "title": "Other"}],
                ),
            ],
        })

        result = find_events_for_file("target_file")

        assert result.events == []

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_calendar_service")
    def test_empty_calendar(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "events.list.execute", {"items": []})

        result = find_events_for_file("target_file")

        assert result.events == []

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_calendar_service")
    def test_multiple_matches(self, mock_svc, _sleep) -> None:
        """Same file attached to multiple meetings."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "events.list.execute", {
            "items": [
                _api_event(
                    event_id="e1",
                    summary="Draft review",
                    attachments=[{"fileId": "doc1", "title": "Draft"}],
                ),
                _api_event(
                    event_id="e2",
                    summary="Final review",
                    attachments=[
                        {"fileId": "doc1", "title": "Draft"},
                        {"fileId": "doc2", "title": "Notes"},
                    ],
                ),
            ],
        })

        result = find_events_for_file("doc1")

        assert len(result.events) == 2
