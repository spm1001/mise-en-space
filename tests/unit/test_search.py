"""
Tests for search tool implementation.

Tests format functions (pure) and do_search wiring (mocked adapters).
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
)
from tools.search import (
    format_drive_result,
    format_gmail_result,
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


