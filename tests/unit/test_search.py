"""
Tests for search tool implementation.

Tests format functions (pure) and do_search wiring (mocked adapters).
"""

from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from models import (
    ActivityActor,
    ActivitySearchResult,
    ActivityTarget,
    CalendarAttachment,
    CalendarAttendee,
    CalendarEvent,
    CalendarSearchResult,
    CommentActivity,
    DriveSearchResult,
    GmailSearchResult,
    EmailContext,
    MiseError,
    ErrorKind,
)
from tools.search import (
    format_drive_result,
    format_gmail_result,
    format_activity_result,
    format_calendar_result,
    _build_meeting_context_index,
    _enrich_drive_results_with_meetings,
    do_search,
)


# ============================================================================
# FORMAT FUNCTIONS (pure, no mocks needed)
# ============================================================================


class TestFormatDriveResult:
    """Test Drive result serialization."""

    def test_basic_fields(self) -> None:
        result = DriveSearchResult(
            file_id="abc123",
            name="Test Doc",
            mime_type="application/vnd.google-apps.document",
            modified_time=datetime(2026, 1, 15, 10, 30),
            owners=["alice@example.com"],
            web_view_link="https://docs.google.com/document/d/abc123",
        )
        formatted = format_drive_result(result)

        assert formatted["id"] == "abc123"
        assert formatted["name"] == "Test Doc"
        assert formatted["mimeType"] == "application/vnd.google-apps.document"
        assert formatted["modified"] == "2026-01-15T10:30:00"
        assert formatted["owners"] == ["alice@example.com"]
        assert formatted["url"] == "https://docs.google.com/document/d/abc123"

    def test_none_modified_time(self) -> None:
        result = DriveSearchResult(
            file_id="abc123",
            name="Test Doc",
            mime_type="application/pdf",
        )
        formatted = format_drive_result(result)
        assert formatted["modified"] is None

    def test_snippet_included(self) -> None:
        result = DriveSearchResult(
            file_id="abc123",
            name="Test Doc",
            mime_type="text/plain",
            snippet="...matching text...",
        )
        formatted = format_drive_result(result)
        assert formatted["snippet"] == "...matching text..."

    def test_email_context_included(self) -> None:
        result = DriveSearchResult(
            file_id="abc123",
            name="Attachment.pdf",
            mime_type="application/pdf",
            email_context=EmailContext(
                message_id="thread789",
                from_address="bob@example.com",
                subject="Re: Project Update",
            ),
        )
        formatted = format_drive_result(result)

        assert "email_context" in formatted
        assert formatted["email_context"]["message_id"] == "thread789"
        assert formatted["email_context"]["from"] == "bob@example.com"
        assert formatted["email_context"]["subject"] == "Re: Project Update"
        assert "fetch" in formatted["email_context"]["hint"]

    def test_no_email_context(self) -> None:
        result = DriveSearchResult(
            file_id="abc123",
            name="Test Doc",
            mime_type="text/plain",
        )
        formatted = format_drive_result(result)
        assert "email_context" not in formatted


class TestFormatGmailResult:
    """Test Gmail result serialization."""

    def test_basic_fields(self) -> None:
        result = GmailSearchResult(
            thread_id="thread456",
            subject="Weekly Update",
            snippet="Here's the latest...",
            date=datetime(2026, 2, 1, 9, 0),
            from_address="alice@example.com",
            message_count=3,
            has_attachments=True,
            attachment_names=["report.pdf", "data.xlsx"],
        )
        formatted = format_gmail_result(result)

        assert formatted["thread_id"] == "thread456"
        assert formatted["subject"] == "Weekly Update"
        assert formatted["date"] == "2026-02-01T09:00:00"
        assert formatted["from"] == "alice@example.com"
        assert formatted["message_count"] == 3
        assert formatted["has_attachments"] is True
        assert formatted["attachment_names"] == ["report.pdf", "data.xlsx"]

    def test_none_date(self) -> None:
        result = GmailSearchResult(
            thread_id="t1",
            subject="No date",
            snippet="",
        )
        formatted = format_gmail_result(result)
        assert formatted["date"] is None


# ============================================================================
# do_search WIRING (mocked adapters)
# ============================================================================


class TestDoSearch:
    """Test search orchestration and error handling."""

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_both_sources_default(self, mock_drive, mock_gmail, mock_write) -> None:
        """Default searches both Drive and Gmail."""
        mock_drive.return_value = [
            DriveSearchResult(file_id="d1", name="Doc", mime_type="text/plain"),
        ]
        mock_gmail.return_value = [
            GmailSearchResult(thread_id="t1", subject="Email", snippet="..."),
        ]
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test query")

        assert result.query == "test query"
        assert result.sources == ["drive", "gmail"]
        assert len(result.drive_results) == 1
        assert len(result.gmail_results) == 1
        assert result.drive_results[0]["id"] == "d1"
        assert result.gmail_results[0]["thread_id"] == "t1"
        mock_drive.assert_called_once()
        mock_gmail.assert_called_once()

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_drive_only(self, mock_drive, mock_gmail, mock_write) -> None:
        """Only Drive searched when sources=['drive']."""
        mock_drive.return_value = []
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["drive"])

        mock_drive.assert_called_once()
        mock_gmail.assert_not_called()
        assert result.sources == ["drive"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_gmail_only(self, mock_drive, mock_gmail, mock_write) -> None:
        """Only Gmail searched when sources=['gmail']."""
        mock_gmail.return_value = []
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["gmail"])

        mock_drive.assert_not_called()
        mock_gmail.assert_called_once()
        assert result.sources == ["gmail"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_drive_error_doesnt_block_gmail(self, mock_drive, mock_gmail, mock_write) -> None:
        """Drive failure still returns Gmail results."""
        mock_drive.side_effect = MiseError(ErrorKind.RATE_LIMITED, "API quota exceeded")
        mock_gmail.return_value = [
            GmailSearchResult(thread_id="t1", subject="Email", snippet="..."),
        ]
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test")

        assert len(result.gmail_results) == 1
        assert len(result.errors) == 1
        assert "Drive" in result.errors[0]
        assert "quota" in result.errors[0]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_gmail_error_doesnt_block_drive(self, mock_drive, mock_gmail, mock_write) -> None:
        """Gmail failure still returns Drive results."""
        mock_drive.return_value = [
            DriveSearchResult(file_id="d1", name="Doc", mime_type="text/plain"),
        ]
        mock_gmail.side_effect = Exception("connection reset")
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test")

        assert len(result.drive_results) == 1
        assert len(result.errors) == 1
        assert "Gmail" in result.errors[0]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_gmail_mise_error_captured(self, mock_drive, mock_gmail, mock_write) -> None:
        """Gmail MiseError uses e.message in error string."""
        mock_drive.return_value = []
        mock_gmail.side_effect = MiseError(ErrorKind.RATE_LIMITED, "quota exceeded")
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test")

        assert len(result.errors) == 1
        assert "quota exceeded" in result.errors[0]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_both_fail_returns_errors(self, mock_drive, mock_gmail, mock_write) -> None:
        """Both sources failing returns both errors."""
        mock_drive.side_effect = Exception("drive boom")
        mock_gmail.side_effect = Exception("gmail boom")
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test")

        assert len(result.errors) == 2
        assert len(result.drive_results) == 0
        assert len(result.gmail_results) == 0

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_max_results_passed_through(self, mock_drive, mock_write) -> None:
        """max_results parameter forwarded to adapter."""
        mock_drive.return_value = []
        mock_write.return_value = "/tmp/fake/search-results.json"

        do_search("test", sources=["drive"], max_results=5)

        _, kwargs = mock_drive.call_args
        assert kwargs["max_results"] == 5

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_query_escaped_for_drive(self, mock_drive, mock_write) -> None:
        """Drive query has single quotes escaped."""
        mock_drive.return_value = []
        mock_write.return_value = "/tmp/fake/search-results.json"

        do_search("user's report", sources=["drive"])

        call_args = mock_drive.call_args[0][0]
        # escape_drive_query escapes single quotes
        assert "\\'" in call_args

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_results_deposited_to_file(self, mock_drive, mock_gmail, mock_write) -> None:
        """Results written to filesystem via write_search_results."""
        mock_drive.return_value = []
        mock_gmail.return_value = []
        mock_write.return_value = "/workspace/mise/search-results.json"

        result = do_search("test")

        mock_write.assert_called_once()
        assert result.path == "/workspace/mise/search-results.json"


# ============================================================================
# SCOPED SEARCH (folder_id)
# ============================================================================


class TestScopedSearch:
    """Test folder-scoped search via folder_id parameter."""

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_folder_id_passed_to_adapter(self, mock_drive, mock_write) -> None:
        """folder_id is forwarded to search_files."""
        mock_drive.return_value = []
        mock_write.return_value = "/tmp/fake/search-results.json"

        do_search("GA4", sources=["drive"], folder_id="abc123")

        _, kwargs = mock_drive.call_args
        assert kwargs.get("folder_id") == "abc123"

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_folder_id_forces_drive_only(self, mock_drive, mock_gmail, mock_write) -> None:
        """When folder_id set, Gmail is excluded even if in sources."""
        mock_drive.return_value = []
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("GA4", sources=["drive", "gmail"], folder_id="abc123")

        mock_gmail.assert_not_called()
        assert "gmail" not in result.sources

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_scope_note_in_cues_with_results(self, mock_drive, mock_write) -> None:
        """Scope note present in cues even when results are found."""
        mock_drive.return_value = [
            DriveSearchResult(file_id="f1", name="tv-conversions.md", mime_type="text/markdown"),
        ]
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("GA4", sources=["drive"], folder_id="folder123")

        assert "scope" in result.cues
        assert "non-recursive" in result.cues["scope"]
        assert "folder123" in result.cues["scope"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_scope_note_in_cues_zero_results(self, mock_drive, mock_write) -> None:
        """Scope note present in cues even on zero results."""
        mock_drive.return_value = []
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("GA4", sources=["drive"], folder_id="folder456")

        assert "scope" in result.cues
        assert "non-recursive" in result.cues["scope"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_no_scope_note_without_folder_id(self, mock_drive, mock_gmail, mock_write) -> None:
        """No cues when folder_id not set (unscoped search)."""
        mock_drive.return_value = []
        mock_gmail.return_value = []
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test")

        assert not result.cues  # empty dict

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_cues_in_to_dict_output(self, mock_drive, mock_write) -> None:
        """Cues appear in the MCP response dict when set."""
        mock_drive.return_value = []
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("GA4", sources=["drive"], folder_id="folder789")
        d = result.to_dict()

        assert "cues" in d
        assert "scope" in d["cues"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_sources_note_when_gmail_dropped(self, mock_drive, mock_gmail, mock_write) -> None:
        """sources_note present in cues when Gmail is excluded due to folder_id."""
        mock_drive.return_value = []
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("GA4", sources=["drive", "gmail"], folder_id="folder123")

        assert "sources_note" in result.cues
        assert "Gmail" in result.cues["sources_note"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_no_sources_note_when_drive_only_from_start(self, mock_drive, mock_write) -> None:
        """No sources_note when caller already requested drive-only with folder_id."""
        mock_drive.return_value = []
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("GA4", sources=["drive"], folder_id="folder123")

        assert "sources_note" not in result.cues

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_unscoped_search_unchanged(self, mock_drive, mock_gmail, mock_write) -> None:
        """folder_id=None produces identical behaviour to omitting it."""
        mock_drive.return_value = []
        mock_gmail.return_value = []
        mock_write.return_value = "/tmp/fake/search-results.json"

        result_none = do_search("test", folder_id=None)
        result_omit = do_search("test")

        assert result_none.sources == result_omit.sources
        assert result_none.cues == result_omit.cues
        # Both calls pass folder_id=None to adapter
        for call in mock_drive.call_args_list:
            assert call.kwargs.get("folder_id") is None


# ============================================================================
# FORMAT ACTIVITY RESULT (pure, no mocks needed)
# ============================================================================


def _make_activity(
    *,
    file_id: str = "doc123",
    file_name: str = "Test Doc",
    action_type: str = "comment",
    actor_name: str = "Alice",
    timestamp: str = "2026-02-23T10:00:00Z",
    mentioned_users: list[str] | None = None,
    mime_type: str = "application/vnd.google-apps.document",
    web_link: str = "https://docs.google.com/document/d/doc123/edit",
) -> CommentActivity:
    """Build a CommentActivity for testing."""
    return CommentActivity(
        activity_id="act/1",
        timestamp=timestamp,
        actor=ActivityActor(name=actor_name),
        target=ActivityTarget(
            file_id=file_id,
            file_name=file_name,
            mime_type=mime_type,
            web_link=web_link,
        ),
        action_type=action_type,
        mentioned_users=mentioned_users or [],
    )


class TestFormatActivityResult:
    """Test Activity result serialization."""

    def test_basic_fields(self) -> None:
        activity = _make_activity()
        formatted = format_activity_result(activity)

        assert formatted["file_id"] == "doc123"
        assert formatted["file_name"] == "Test Doc"
        assert formatted["action_type"] == "comment"
        assert formatted["actor"] == "Alice"
        assert formatted["timestamp"] == "2026-02-23T10:00:00Z"
        assert formatted["url"] == "https://docs.google.com/document/d/doc123/edit"
        assert "mentioned_users" not in formatted  # Empty list omitted

    def test_with_mentions(self) -> None:
        activity = _make_activity(mentioned_users=["Bob", "Carol"])
        formatted = format_activity_result(activity)

        assert formatted["mentioned_users"] == ["Bob", "Carol"]

    def test_mime_type_included(self) -> None:
        activity = _make_activity(mime_type="application/vnd.google-apps.spreadsheet")
        formatted = format_activity_result(activity)

        assert formatted["mime_type"] == "application/vnd.google-apps.spreadsheet"


# ============================================================================
# ACTIVITY SEARCH ORCHESTRATION
# ============================================================================


class TestActivitySearch:
    """Test activity source in do_search."""

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_comment_activities')
    def test_activity_only(self, mock_activity, mock_write) -> None:
        """Activity-only search returns activity results."""
        mock_activity.return_value = ActivitySearchResult(
            activities=[_make_activity()],
        )
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["activity"])

        assert result.sources == ["activity"]
        assert len(result.activity_results) == 1
        assert result.activity_results[0]["file_id"] == "doc123"
        mock_activity.assert_called_once()

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_comment_activities')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_activity_with_drive_and_gmail(self, mock_drive, mock_gmail, mock_activity, mock_write) -> None:
        """All three sources can run together."""
        mock_drive.return_value = [
            DriveSearchResult(file_id="d1", name="Doc", mime_type="text/plain"),
        ]
        mock_gmail.return_value = [
            GmailSearchResult(thread_id="t1", subject="Email", snippet="..."),
        ]
        mock_activity.return_value = ActivitySearchResult(
            activities=[_make_activity()],
        )
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["drive", "gmail", "activity"])

        assert len(result.drive_results) == 1
        assert len(result.gmail_results) == 1
        assert len(result.activity_results) == 1
        mock_drive.assert_called_once()
        mock_gmail.assert_called_once()
        mock_activity.assert_called_once()

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_comment_activities')
    def test_activity_error_captured(self, mock_activity, mock_write) -> None:
        """Activity failure captured in errors, not raised."""
        mock_activity.side_effect = MiseError(
            ErrorKind.NETWORK_ERROR, "API timeout"
        )
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["activity"])

        assert result.activity_results == []
        assert any("Activity search failed" in e for e in result.errors)

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_comment_activities')
    def test_activity_empty_results(self, mock_activity, mock_write) -> None:
        """Empty activity results handled cleanly."""
        mock_activity.return_value = ActivitySearchResult(activities=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["activity"])

        assert result.activity_results == []
        assert result.errors == []

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_comment_activities')
    def test_activity_max_results_forwarded(self, mock_activity, mock_write) -> None:
        """max_results passed as page_size to activity search."""
        mock_activity.return_value = ActivitySearchResult(activities=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        do_search("test", sources=["activity"], max_results=10)

        mock_activity.assert_called_once_with(page_size=10)

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_comment_activities')
    @patch('tools.search.search_files')
    def test_activity_excluded_by_folder_id(self, mock_drive, mock_activity, mock_write) -> None:
        """Activity source dropped when folder_id is set."""
        mock_drive.return_value = []
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["drive", "activity"], folder_id="folder123")

        assert result.sources == ["drive"]
        mock_activity.assert_not_called()
        assert "Activity" in result.cues.get("sources_note", "")

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_comment_activities')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_activity_error_doesnt_block_others(self, mock_drive, mock_gmail, mock_activity, mock_write) -> None:
        """Activity failure doesn't block Drive/Gmail results."""
        mock_drive.return_value = [
            DriveSearchResult(file_id="d1", name="Doc", mime_type="text/plain"),
        ]
        mock_gmail.return_value = [
            GmailSearchResult(thread_id="t1", subject="Email", snippet="..."),
        ]
        mock_activity.side_effect = Exception("API broke")
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["drive", "gmail", "activity"])

        assert len(result.drive_results) == 1
        assert len(result.gmail_results) == 1
        assert result.activity_results == []
        assert any("Activity search failed" in e for e in result.errors)


class TestSearchResultModel:
    """Test SearchResult model with activity_results."""

    def test_full_results_includes_activity(self) -> None:
        from models import SearchResult
        result = SearchResult(
            query="test",
            sources=["activity"],
            activity_results=[{"file_id": "f1", "action_type": "comment"}],
        )
        full = result.full_results()
        assert "activity_results" in full
        assert len(full["activity_results"]) == 1

    def test_to_dict_includes_activity_count(self) -> None:
        from models import SearchResult
        result = SearchResult(
            query="test",
            sources=["activity"],
            activity_results=[{"file_id": "f1", "action_type": "comment", "file_name": "Doc", "actor": "Alice", "timestamp": "2026-02-23"}],
            path="/tmp/fake/results.json",
        )
        d = result.to_dict()
        assert d["activity_count"] == 1

    def test_preview_includes_activity(self) -> None:
        from models import SearchResult
        result = SearchResult(
            query="test",
            sources=["activity"],
            activity_results=[{
                "file_id": "f1",
                "file_name": "Doc",
                "action_type": "comment",
                "actor": "Alice",
                "timestamp": "2026-02-23",
                "mentioned_users": ["Bob"],
            }],
            path="/tmp/fake/results.json",
        )
        d = result.to_dict()
        assert "activity" in d["preview"]
        assert d["preview"]["activity"][0]["mentioned_users"] == ["Bob"]

    def test_full_results_includes_calendar(self) -> None:
        from models import SearchResult
        result = SearchResult(
            query="test",
            sources=["calendar"],
            calendar_results=[{"event_id": "e1", "summary": "Standup"}],
        )
        full = result.full_results()
        assert "calendar_results" in full

    def test_to_dict_includes_calendar_count(self) -> None:
        from models import SearchResult
        result = SearchResult(
            query="test",
            sources=["calendar"],
            calendar_results=[{"event_id": "e1", "summary": "Standup", "start_time": "2026-02-23", "attendee_count": 3}],
            path="/tmp/fake/results.json",
        )
        d = result.to_dict()
        assert d["calendar_count"] == 1

    def test_preview_includes_calendar(self) -> None:
        from models import SearchResult
        result = SearchResult(
            query="test",
            sources=["calendar"],
            calendar_results=[{
                "event_id": "e1",
                "summary": "Planning",
                "start_time": "2026-02-23T10:00:00Z",
                "attendee_count": 5,
                "attachment_count": 2,
                "meet_link": "https://meet.google.com/abc",
            }],
            path="/tmp/fake/results.json",
        )
        d = result.to_dict()
        assert "calendar" in d["preview"]
        item = d["preview"]["calendar"][0]
        assert item["summary"] == "Planning"
        assert item["has_meet"] is True
        assert item["attachment_count"] == 2


# ============================================================================
# FORMAT CALENDAR RESULT (pure, no mocks needed)
# ============================================================================


def _make_calendar_event(
    *,
    event_id: str = "evt1",
    summary: str = "Team Sync",
    start_time: str = "2026-02-23T10:00:00Z",
    end_time: str = "2026-02-23T11:00:00Z",
    attendees: list[CalendarAttendee] | None = None,
    attachments: list[CalendarAttachment] | None = None,
    meet_link: str | None = None,
    organizer_email: str | None = "boss@example.com",
) -> CalendarEvent:
    """Build a CalendarEvent for testing."""
    return CalendarEvent(
        event_id=event_id,
        summary=summary,
        start_time=start_time,
        end_time=end_time,
        attendees=attendees or [],
        attachments=attachments or [],
        meet_link=meet_link,
        organizer_email=organizer_email,
    )


class TestFormatCalendarResult:
    """Test Calendar result serialization."""

    def test_basic_fields(self) -> None:
        event = _make_calendar_event()
        formatted = format_calendar_result(event)

        assert formatted["event_id"] == "evt1"
        assert formatted["summary"] == "Team Sync"
        assert formatted["start_time"] == "2026-02-23T10:00:00Z"
        assert formatted["organizer"] == "boss@example.com"
        assert formatted["attendee_count"] == 0
        assert "attachments" not in formatted
        assert "meet_link" not in formatted

    def test_with_attendees(self) -> None:
        event = _make_calendar_event(attendees=[
            CalendarAttendee(email="alice@example.com", display_name="Alice", response_status="accepted"),
            CalendarAttendee(email="room@resource.calendar.google.com", is_resource=True),
        ])
        formatted = format_calendar_result(event)

        assert formatted["attendee_count"] == 1  # Room excluded
        assert len(formatted["attendees"]) == 1
        assert formatted["attendees"][0]["email"] == "alice@example.com"

    def test_with_attachments(self) -> None:
        event = _make_calendar_event(attachments=[
            CalendarAttachment(file_id="doc1", title="Agenda"),
            CalendarAttachment(file_id="doc2", title="Notes"),
        ])
        formatted = format_calendar_result(event)

        assert formatted["attachment_count"] == 2
        assert formatted["attachments"][0]["file_id"] == "doc1"

    def test_with_meet_link(self) -> None:
        event = _make_calendar_event(meet_link="https://meet.google.com/abc-defg")
        formatted = format_calendar_result(event)

        assert formatted["meet_link"] == "https://meet.google.com/abc-defg"


# ============================================================================
# MEETING CONTEXT INDEX (pure)
# ============================================================================


class TestBuildMeetingContextIndex:
    """Test _build_meeting_context_index cross-referencing."""

    def test_builds_index_from_attachments(self) -> None:
        events = [
            _make_calendar_event(
                summary="Review",
                attachments=[CalendarAttachment(file_id="doc1", title="Draft")],
            ),
        ]
        index = _build_meeting_context_index(events)

        assert "doc1" in index
        assert index["doc1"][0]["summary"] == "Review"

    def test_multiple_events_same_file(self) -> None:
        events = [
            _make_calendar_event(
                event_id="e1", summary="Draft review",
                attachments=[CalendarAttachment(file_id="doc1", title="Draft")],
            ),
            _make_calendar_event(
                event_id="e2", summary="Final review",
                attachments=[CalendarAttachment(file_id="doc1", title="Draft")],
            ),
        ]
        index = _build_meeting_context_index(events)

        assert len(index["doc1"]) == 2

    def test_events_without_attachments_skipped(self) -> None:
        events = [_make_calendar_event()]  # No attachments
        index = _build_meeting_context_index(events)

        assert index == {}

    def test_empty_events(self) -> None:
        assert _build_meeting_context_index([]) == {}


class TestEnrichDriveResults:
    """Test _enrich_drive_results_with_meetings mutation."""

    def test_enriches_matching_file(self) -> None:
        drive_results = [{"id": "doc1", "name": "Draft"}]
        index = {"doc1": [{"summary": "Review", "start_time": "2026-02-23T10:00:00Z"}]}

        _enrich_drive_results_with_meetings(drive_results, index)

        assert "meeting_context" in drive_results[0]
        assert drive_results[0]["meeting_context"][0]["summary"] == "Review"

    def test_non_matching_file_unchanged(self) -> None:
        drive_results = [{"id": "doc2", "name": "Other"}]
        index = {"doc1": [{"summary": "Review"}]}

        _enrich_drive_results_with_meetings(drive_results, index)

        assert "meeting_context" not in drive_results[0]

    def test_empty_index(self) -> None:
        drive_results = [{"id": "doc1", "name": "Draft"}]

        _enrich_drive_results_with_meetings(drive_results, {})

        assert "meeting_context" not in drive_results[0]


# ============================================================================
# CALENDAR SEARCH ORCHESTRATION
# ============================================================================


class TestCalendarSearch:
    """Test calendar source in do_search."""

    @patch('tools.search.write_search_results')
    @patch('tools.search.list_events')
    def test_calendar_only(self, mock_calendar, mock_write) -> None:
        """Calendar-only search returns events."""
        mock_calendar.return_value = CalendarSearchResult(
            events=[_make_calendar_event()],
        )
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["calendar"])

        assert result.sources == ["calendar"]
        assert len(result.calendar_results) == 1
        assert result.calendar_results[0]["summary"] == "Team Sync"

    @patch('tools.search.write_search_results')
    @patch('tools.search.list_events')
    @patch('tools.search.search_files')
    def test_drive_enriched_with_calendar(self, mock_drive, mock_calendar, mock_write) -> None:
        """Drive results get meeting_context when calendar has matching attachments."""
        mock_drive.return_value = [
            DriveSearchResult(file_id="doc1", name="Agenda", mime_type="text/plain"),
        ]
        mock_calendar.return_value = CalendarSearchResult(
            events=[
                _make_calendar_event(
                    summary="Team standup",
                    attachments=[CalendarAttachment(file_id="doc1", title="Agenda")],
                    attendees=[CalendarAttendee(email="alice@example.com")],
                ),
            ],
        )
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("agenda", sources=["drive", "calendar"])

        assert len(result.drive_results) == 1
        assert "meeting_context" in result.drive_results[0]
        assert result.drive_results[0]["meeting_context"][0]["summary"] == "Team standup"

    @patch('tools.search.write_search_results')
    @patch('tools.search.list_events')
    @patch('tools.search.search_files')
    def test_no_enrichment_when_no_matches(self, mock_drive, mock_calendar, mock_write) -> None:
        """No meeting_context added when calendar has no matching attachments."""
        mock_drive.return_value = [
            DriveSearchResult(file_id="doc1", name="Random", mime_type="text/plain"),
        ]
        mock_calendar.return_value = CalendarSearchResult(
            events=[_make_calendar_event()],  # No attachments
        )
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["drive", "calendar"])

        assert "meeting_context" not in result.drive_results[0]

    @patch('tools.search.write_search_results')
    @patch('tools.search.list_events')
    def test_calendar_error_captured(self, mock_calendar, mock_write) -> None:
        """Calendar failure captured in errors."""
        mock_calendar.side_effect = MiseError(ErrorKind.NETWORK_ERROR, "timeout")
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["calendar"])

        assert result.calendar_results == []
        assert any("Calendar search failed" in e for e in result.errors)

    @patch('tools.search.write_search_results')
    @patch('tools.search.list_events')
    @patch('tools.search.search_files')
    def test_calendar_excluded_by_folder_id(self, mock_drive, mock_calendar, mock_write) -> None:
        """Calendar source dropped when folder_id is set."""
        mock_drive.return_value = []
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["drive", "calendar"], folder_id="folder123")

        assert result.sources == ["drive"]
        mock_calendar.assert_not_called()
        assert "Calendar" in result.cues.get("sources_note", "")


