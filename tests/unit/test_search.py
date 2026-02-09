"""
Tests for search tool implementation.

Tests format functions (pure), do_search wiring (mocked adapters),
and do_search_activity routing.
"""

from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from models import (
    DriveSearchResult,
    GmailSearchResult,
    EmailContext,
    MiseError,
    ErrorKind,
    CommentActivity,
    ActivityActor,
    ActivityTarget,
    ActivitySearchResult,
)
from tools.search import (
    format_drive_result,
    format_gmail_result,
    format_activity_result,
    do_search,
    do_search_activity,
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


class TestFormatActivityResult:
    """Test activity result serialization."""

    def test_basic_fields(self) -> None:
        activity = CommentActivity(
            activity_id="act1",
            timestamp="2026-02-01T10:00:00Z",
            actor=ActivityActor(name="Alice", email="alice@example.com"),
            target=ActivityTarget(
                file_id="doc123",
                file_name="Design Doc",
                mime_type="application/vnd.google-apps.document",
                web_link="https://docs.google.com/document/d/doc123",
            ),
            action_type="comment",
            mentioned_users=["bob@example.com"],
        )
        formatted = format_activity_result(activity)

        assert formatted["activity_id"] == "act1"
        assert formatted["actor"]["name"] == "Alice"
        assert formatted["actor"]["email"] == "alice@example.com"
        assert formatted["target"]["file_id"] == "doc123"
        assert formatted["action_type"] == "comment"
        assert formatted["mentioned_users"] == ["bob@example.com"]


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
        mock_write.return_value = "/workspace/mise-fetch/search-results.json"

        result = do_search("test")

        mock_write.assert_called_once()
        assert result.path == "/workspace/mise-fetch/search-results.json"


# ============================================================================
# do_search_activity ROUTING
# ============================================================================


class TestDoSearchActivity:
    """Test activity search routing and error handling."""

    @patch('tools.search.search_comment_activities')
    def test_default_comment_search(self, mock_search) -> None:
        """Default filter searches comment activities."""
        mock_search.return_value = ActivitySearchResult(
            activities=[
                CommentActivity(
                    activity_id="a1",
                    timestamp="2026-02-01T10:00:00Z",
                    actor=ActivityActor(name="Alice"),
                    target=ActivityTarget(file_id="f1", file_name="Doc"),
                    action_type="comment",
                ),
            ],
            next_page_token=None,
            warnings=[],
        )

        result = do_search_activity()

        assert result["activity_count"] == 1
        assert result["activities"][0]["activity_id"] == "a1"
        assert "error" not in result
        mock_search.assert_called_once()

    @patch('tools.search.get_file_activities')
    def test_file_specific_activity(self, mock_get) -> None:
        """file_id routes to get_file_activities."""
        mock_get.return_value = ActivitySearchResult(
            activities=[],
            next_page_token=None,
            warnings=[],
        )

        result = do_search_activity(file_id="doc123")

        mock_get.assert_called_once_with("doc123", page_size=50, filter_type="comments")
        assert result["activity_count"] == 0

    def test_cross_file_non_comment_rejected(self) -> None:
        """Cross-file search with non-comment filter returns error."""
        result = do_search_activity(filter_type="edits")

        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "comments" in result["message"]

    @patch('tools.search.get_file_activities')
    def test_file_specific_edits_allowed(self, mock_get) -> None:
        """File-specific search allows edits filter."""
        mock_get.return_value = ActivitySearchResult(
            activities=[], next_page_token=None, warnings=[],
        )

        result = do_search_activity(filter_type="edits", file_id="doc123")

        mock_get.assert_called_once_with("doc123", page_size=50, filter_type="edits")
        assert "error" not in result

    @patch('tools.search.search_comment_activities')
    def test_mise_error_handled(self, mock_search) -> None:
        """MiseError from adapter returns structured error."""
        mock_search.side_effect = MiseError(ErrorKind.AUTH_EXPIRED, "Token expired")

        result = do_search_activity()

        assert result["error"] is True
        assert result["kind"] == "auth_expired"
        assert "Token expired" in result["message"]

    @patch('tools.search.search_comment_activities')
    def test_unexpected_error_handled(self, mock_search) -> None:
        """Unexpected exception returns generic error."""
        mock_search.side_effect = RuntimeError("something broke")

        result = do_search_activity()

        assert result["error"] is True
        assert result["kind"] == "unknown"
        assert "something broke" in result["message"]

    @patch('tools.search.search_comment_activities')
    def test_pagination_token_returned(self, mock_search) -> None:
        """Next page token passed through when present."""
        mock_search.return_value = ActivitySearchResult(
            activities=[], next_page_token="next123", warnings=[],
        )

        result = do_search_activity()

        assert result["next_page_token"] == "next123"

    @patch('tools.search.search_comment_activities')
    def test_warnings_returned(self, mock_search) -> None:
        """Warnings from adapter included in response."""
        mock_search.return_value = ActivitySearchResult(
            activities=[], next_page_token=None,
            warnings=["Some activities may be missing"],
        )

        result = do_search_activity()

        assert result["warnings"] == ["Some activities may be missing"]
