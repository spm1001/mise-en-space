"""
Tests for Calendar API adapter and models.
"""

from unittest.mock import patch, MagicMock

from models import (
    CalendarAttachment,
    CalendarAttendee,
    CalendarEvent,
    CalendarSearchResult,
    InviteState,
)
from adapters.calendar import (
    _parse_attendee,
    _parse_attachment,
    _parse_event,
    list_events,
    get_event_by_ical_uid,
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
        assert result.truncated is False
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
# list_events (mocked HTTP client)
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
    """Test list_events with mocked HTTP client."""

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_sync_client")
    def test_basic_list(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "items": [_api_event()],
        }

        result = list_events()

        assert isinstance(result, CalendarSearchResult)
        assert len(result.events) == 1
        assert result.events[0].summary == "Test Event"

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_sync_client")
    def test_empty_response(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {}

        result = list_events()

        assert result.events == []
        assert result.truncated is False

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_sync_client")
    def test_internal_pagination_follows_token(self, mock_get_client, _sleep) -> None:
        """The whole window is scanned across pages before any cap applies —
        a single capped page is how the cap ate the future (mise-bidopi)."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.side_effect = [
            {"items": [_api_event(event_id="e1")], "nextPageToken": "page2"},
            {"items": [_api_event(event_id="e2")]},
        ]

        result = list_events()

        assert len(result.events) == 2
        assert result.truncated is False
        # Second call carried the token
        second_call = mock_client.get_json.call_args_list[1]
        assert second_call.kwargs["params"]["pageToken"] == "page2"

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_sync_client")
    def test_query_passed_as_q(self, mock_get_client, _sleep) -> None:
        """The user's query reaches the API — the original bidopi failure was
        that it never did, so results were unrelated events."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {"items": []}

        list_events(query="Gareth")
        assert mock_client.get_json.call_args.kwargs["params"]["q"] == "Gareth"

        list_events(query="   ")
        assert "q" not in mock_client.get_json.call_args.kwargs["params"]

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_sync_client")
    def test_max_results_does_not_shrink_page_size(self, mock_get_client, _sleep) -> None:
        """max_results caps the RETURNED list, not the API page — the scan
        must see the whole window regardless."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {"items": []}

        list_events(max_results=5)

        call_kwargs = mock_client.get_json.call_args.kwargs
        assert call_kwargs["params"]["maxResults"] == 250

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_sync_client")
    def test_cap_keeps_events_nearest_now(self, mock_get_client, _sleep) -> None:
        """When more events match than max_results, the nearest-to-now win
        (in chronological order) and the result is flagged truncated —
        tomorrow's meeting must survive a busy week (mise-bidopi)."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)

        def iso(delta: timedelta) -> str:
            return (now + delta).isoformat()

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "items": [
                _api_event(event_id="old", summary="Last week",
                           start=iso(timedelta(days=-6)), end=iso(timedelta(days=-6, hours=1))),
                _api_event(event_id="recent", summary="Earlier today",
                           start=iso(timedelta(hours=-1)), end=iso(timedelta())),
                _api_event(event_id="soon", summary="Tomorrow's meeting",
                           start=iso(timedelta(hours=20)), end=iso(timedelta(hours=21))),
                _api_event(event_id="far", summary="Next week",
                           start=iso(timedelta(days=6)), end=iso(timedelta(days=6, hours=1))),
            ],
        }

        result = list_events(max_results=2)

        assert result.truncated is True
        assert [e.event_id for e in result.events] == ["recent", "soon"]  # chronological

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_sync_client")
    def test_single_events_and_order(self, mock_get_client, _sleep) -> None:
        """Verifies singleEvents=true and orderBy=startTime are set."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {"items": []}

        list_events()

        call_kwargs = mock_client.get_json.call_args.kwargs
        assert call_kwargs["params"]["singleEvents"] == "true"
        assert call_kwargs["params"]["orderBy"] == "startTime"
        # URL should target primary calendar
        assert "primary/events" in mock_client.get_json.call_args.args[0]

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_sync_client")
    def test_multiple_events(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "items": [
                _api_event(event_id="e1", summary="Morning standup"),
                _api_event(event_id="e2", summary="Lunch"),
                _api_event(event_id="e3", summary="Review"),
            ],
        }

        result = list_events()

        assert len(result.events) == 3
        summaries = [e.summary for e in result.events]
        assert summaries == ["Morning standup", "Lunch", "Review"]




class TestGetEventByIcalUid:
    """Live invite-state lookup (mise-pinodi). An invite email is a frozen
    snapshot; this reads the CURRENT event state by iCalUID."""

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_sync_client")
    def test_showdeleted_is_passed(self, mock_get_client, _sleep) -> None:
        """showDeleted=true is LOAD-BEARING: without it a cancelled event
        returns 0 items (invisible), so the request MUST include it."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {"items": []}

        get_event_by_ical_uid("uid@google.com")

        _args, kwargs = mock_client.get_json.call_args
        assert kwargs["params"]["showDeleted"] == "true"
        assert kwargs["params"]["iCalUID"] == "uid@google.com"

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_sync_client")
    def test_cancelled_event(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {"items": [{
            "status": "cancelled",
            "start": {"dateTime": "2026-06-29T15:00:00+01:00"},
            "updated": "2026-06-26T15:59:41Z",
            "attendees": [
                {"email": "other@x.com", "responseStatus": "accepted"},
                {"email": "me@x.com", "self": True, "responseStatus": "needsAction"},
            ],
        }]}

        state = get_event_by_ical_uid("uid@google.com")

        assert isinstance(state, InviteState)
        assert state.status == "cancelled"
        assert state.my_response == "needsAction"
        assert state.current_start == "2026-06-29T15:00:00+01:00"
        assert state.cancelled_at == "2026-06-26T15:59:41Z"

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_sync_client")
    def test_confirmed_event_has_no_cancelled_at(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {"items": [{
            "status": "confirmed",
            "start": {"dateTime": "2026-07-01T09:00:00+01:00"},
            "updated": "2026-06-20T10:00:00Z",
            "attendees": [{"email": "me@x.com", "self": True, "responseStatus": "accepted"}],
        }]}

        state = get_event_by_ical_uid("uid@google.com")

        assert state.status == "confirmed"
        assert state.my_response == "accepted"
        assert state.cancelled_at is None  # only surfaced when cancelled

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_sync_client")
    def test_rescheduled_start_is_current(self, mock_get_client, _sleep) -> None:
        """current_start is always the live start — covers reschedule for free."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {"items": [{
            "status": "confirmed",
            "start": {"dateTime": "2026-08-15T14:30:00+01:00"},
            "attendees": [],
        }]}

        state = get_event_by_ical_uid("uid@google.com")
        assert state.current_start == "2026-08-15T14:30:00+01:00"
        assert state.my_response is None  # no self attendee

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_sync_client")
    def test_no_matching_event_returns_none(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {"items": []}

        assert get_event_by_ical_uid("nonexistent@google.com") is None

    @patch("retry.time.sleep")
    @patch("adapters.calendar.get_sync_client")
    def test_all_day_event_uses_date(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {"items": [{
            "status": "confirmed",
            "start": {"date": "2026-09-01"},
            "attendees": [],
        }]}

        state = get_event_by_ical_uid("uid@google.com")
        assert state.current_start == "2026-09-01"
