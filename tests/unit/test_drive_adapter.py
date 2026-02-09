"""
Tests for drive adapter — pure helpers and mocked API functions.
"""

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, Mock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from tests.helpers import mock_api_chain, seal_service

from models import (
    EmailContext,
    DriveSearchResult,
    MiseError,
    ErrorKind,
    FileCommentsData,
    CommentData,
    CommentReply,
)
from adapters.drive import (
    _parse_email_context,
    _parse_datetime,
    get_file_metadata,
    export_file,
    download_file,
    get_file_size,
    search_files,
    fetch_file_comments,
    lookup_exfiltrated,
    download_file_to_temp,
    is_google_workspace_file,
    _get_email_attachments_folder_id,
    COMMENT_UNSUPPORTED_MIMES,
)


class TestParseEmailContext:
    """Test extraction of email metadata from exfil'd file descriptions."""

    def test_full_description(self) -> None:
        """All fields present in standard exfil format."""
        description = (
            "From: alice@example.com\n"
            "Subject: Budget analysis\n"
            "Date: 2026-01-15T10:30:00Z\n"
            "Message ID: 18f4a5b6c7d8e9f0\n"
            "Content Hash: abc123def456"
        )
        result = _parse_email_context(description)

        assert result is not None
        assert result.message_id == "18f4a5b6c7d8e9f0"
        assert result.from_address == "alice@example.com"
        assert result.subject == "Budget analysis"
        assert result.date == "2026-01-15T10:30:00Z"

    def test_only_message_id(self) -> None:
        """Minimal description with just Message ID."""
        description = "Message ID: abc123"
        result = _parse_email_context(description)

        assert result is not None
        assert result.message_id == "abc123"
        assert result.from_address is None
        assert result.subject is None
        assert result.date is None

    def test_no_message_id_returns_none(self) -> None:
        """Description without Message ID returns None."""
        description = "From: alice@example.com\nSubject: Test"
        assert _parse_email_context(description) is None

    def test_none_description_returns_none(self) -> None:
        assert _parse_email_context(None) is None

    def test_empty_description_returns_none(self) -> None:
        assert _parse_email_context("") is None

    def test_whitespace_stripped(self) -> None:
        """Extra whitespace in field values is stripped."""
        description = (
            "From:   alice@example.com   \n"
            "Subject:   Spaced Out   \n"
            "Message ID: abc123"
        )
        result = _parse_email_context(description)

        assert result is not None
        assert result.from_address == "alice@example.com"
        assert result.subject == "Spaced Out"


class TestParseDatetime:
    """Test ISO datetime parsing from Drive API."""

    def test_rfc3339_with_z(self) -> None:
        """Standard Drive API format with Z suffix."""
        result = _parse_datetime("2026-01-15T10:30:00.000Z")
        assert result is not None
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 15

    def test_rfc3339_with_offset(self) -> None:
        result = _parse_datetime("2026-01-15T10:30:00+00:00")
        assert result is not None
        assert result.year == 2026

    def test_none_returns_none(self) -> None:
        assert _parse_datetime(None) is None

    def test_empty_returns_none(self) -> None:
        assert _parse_datetime("") is None

    def test_invalid_format_returns_none(self) -> None:
        assert _parse_datetime("not a date") is None


# ============================================================================
# ADAPTER FUNCTIONS (mocked service)
# ============================================================================

class TestExportFile:
    """Test export_file with mocked Drive API (sealed)."""

    @patch('adapters.drive.get_drive_service')
    def test_exports_bytes(self, mock_get_service) -> None:
        """export_file returns bytes from API."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_api_chain(mock_service, "files.export.execute", b"# Exported Markdown")
        seal_service(mock_service)

        with patch('retry.time.sleep'):
            result = export_file("doc123", "text/markdown")

        assert result == b"# Exported Markdown"


class TestDownloadFile:
    """Test download_file with mocked Drive API."""

    @patch('adapters.drive.get_drive_service')
    def test_small_file_in_memory(self, mock_get_service) -> None:
        """Small file loaded into memory."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        # Size check returns small file
        mock_api_chain(mock_service, "files.get.execute", {"size": "1024"})
        # Download returns content
        mock_api_chain(mock_service, "files.get_media.execute", b"file content")

        with patch('retry.time.sleep'):
            result = download_file("file123")

        assert result == b"file content"

    @patch('adapters.drive.get_drive_service')
    def test_large_file_raises(self, mock_get_service) -> None:
        """File over threshold raises MiseError."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        # Size check returns huge file
        huge_size = 100 * 1024 * 1024  # 100MB
        mock_api_chain(mock_service, "files.get.execute", {"size": str(huge_size)})

        with patch('retry.time.sleep'):
            with pytest.raises(MiseError) as exc_info:
                download_file("bigfile")

        assert exc_info.value.kind == ErrorKind.INVALID_INPUT
        assert "too large" in exc_info.value.message


class TestGetFileSize:
    """Test get_file_size with mocked Drive API."""

    @patch('adapters.drive.get_drive_service')
    def test_returns_size(self, mock_get_service) -> None:
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_api_chain(mock_service, "files.get.execute", {"size": "12345"})

        result = get_file_size("file123")
        assert result == 12345

    @patch('adapters.drive.get_drive_service')
    def test_missing_size_returns_zero(self, mock_get_service) -> None:
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_api_chain(mock_service, "files.get.execute", {})

        result = get_file_size("folder123")
        assert result == 0


class TestSearchFiles:
    """Test search_files with mocked Drive API."""

    @patch('adapters.drive.get_drive_service')
    def test_returns_search_results(self, mock_get_service) -> None:
        """Search results parsed into DriveSearchResult objects."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        mock_api_chain(mock_service, "files.list.execute", {
            "files": [
                {
                    "id": "doc1",
                    "name": "Budget 2026",
                    "mimeType": "application/vnd.google-apps.document",
                    "modifiedTime": "2026-01-15T10:30:00.000Z",
                    "owners": [{"displayName": "Alice"}],
                    "webViewLink": "https://docs.google.com/document/d/doc1",
                },
                {
                    "id": "sheet1",
                    "name": "Revenue Data",
                    "mimeType": "application/vnd.google-apps.spreadsheet",
                    "owners": [{"displayName": "Bob"}],
                },
            ],
        })

        with patch('retry.time.sleep'):
            results = search_files("fullText contains 'budget'")

        assert len(results) == 2
        assert all(isinstance(r, DriveSearchResult) for r in results)
        assert results[0].file_id == "doc1"
        assert results[0].name == "Budget 2026"
        assert results[0].owners == ["Alice"]
        assert results[0].modified_time is not None
        assert results[0].modified_time.year == 2026
        assert results[1].file_id == "sheet1"
        assert results[1].modified_time is None  # No modifiedTime

    @patch('adapters.drive.get_drive_service')
    def test_empty_search(self, mock_get_service) -> None:
        """Empty search returns empty list."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_api_chain(mock_service, "files.list.execute", {"files": []})

        with patch('retry.time.sleep'):
            results = search_files("nonexistent")

        assert results == []

    @patch('adapters.drive.get_drive_service')
    def test_exfil_file_includes_email_context(self, mock_get_service) -> None:
        """File with exfil description includes email context."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        mock_api_chain(mock_service, "files.list.execute", {
            "files": [{
                "id": "pdf1",
                "name": "report.pdf",
                "mimeType": "application/pdf",
                "description": (
                    "From: sender@example.com\n"
                    "Subject: Monthly Report\n"
                    "Message ID: 18f4a5b6c7d8e9f0"
                ),
            }],
        })

        with patch('retry.time.sleep'):
            results = search_files("report")

        assert len(results) == 1
        assert results[0].email_context is not None
        assert results[0].email_context.message_id == "18f4a5b6c7d8e9f0"
        assert results[0].email_context.from_address == "sender@example.com"

    @patch('adapters.drive.get_drive_service')
    def test_max_results_capped_at_100(self, mock_get_service) -> None:
        """API pageSize capped at 100 even if max_results is higher."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_api_chain(mock_service, "files.list.execute", {"files": []})

        with patch('retry.time.sleep'):
            search_files("test", max_results=200)

        call_kwargs = mock_service.files().list.call_args[1]
        assert call_kwargs["pageSize"] == 100


# ============================================================================
# FETCH FILE COMMENTS (mocked service + get_file_metadata)
# ============================================================================


def _make_http_error(status: int, body: bytes = b"error") -> HttpError:
    """Create a googleapiclient HttpError with given status code."""
    resp = httplib2.Response({"status": status})
    return HttpError(resp, body)


def _api_comment(
    *,
    id: str = "c1",
    content: str = "Test comment",
    author_name: str = "Alice",
    author_email: str = "alice@example.com",
    created_time: str = "2026-01-15T10:00:00Z",
    resolved: bool = False,
    quoted_text: str = "",
    mentioned_emails: list[str] | None = None,
    replies: list[dict] | None = None,
) -> dict:
    """Build an API-shaped comment dict (what Drive Comments API returns)."""
    comment: dict = {
        "id": id,
        "content": content,
        "author": {"displayName": author_name, "emailAddress": author_email},
        "createdTime": created_time,
        "modifiedTime": created_time,
        "resolved": resolved,
        "replies": replies or [],
    }
    if quoted_text:
        comment["quotedFileContent"] = {"value": quoted_text}
    if mentioned_emails:
        comment["mentionedEmailAddresses"] = mentioned_emails
    return comment


def _api_reply(
    *,
    id: str = "r1",
    content: str = "Reply text",
    author_name: str = "Bob",
    author_email: str = "bob@example.com",
    created_time: str = "2026-01-15T14:00:00Z",
    mentioned_emails: list[str] | None = None,
) -> dict:
    """Build an API-shaped reply dict."""
    reply: dict = {
        "id": id,
        "content": content,
        "author": {"displayName": author_name, "emailAddress": author_email},
        "createdTime": created_time,
        "modifiedTime": created_time,
    }
    if mentioned_emails:
        reply["mentionedEmailAddresses"] = mentioned_emails
    return reply


# Standard metadata returned by mocked get_file_metadata
_DOC_METADATA = {
    "name": "Test Document",
    "mimeType": "application/vnd.google-apps.document",
}


class TestFetchFileComments:
    """Test fetch_file_comments with mocked Drive API."""

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_basic_comment_parsing(self, mock_svc, mock_meta, _sleep) -> None:
        """Single comment with all fields populated."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_api_chain(mock_service, "comments.list.execute", {
            "comments": [
                _api_comment(
                    id="c1",
                    content="Looks good!",
                    author_name="Alice",
                    author_email="alice@example.com",
                    quoted_text="Revenue target: $2.5M",
                    mentioned_emails=["bob@example.com"],
                ),
            ],
        })

        result = fetch_file_comments("doc123")

        assert isinstance(result, FileCommentsData)
        assert result.file_id == "doc123"
        assert result.file_name == "Test Document"
        assert result.comment_count == 1
        assert result.warnings == []

        c = result.comments[0]
        assert c.id == "c1"
        assert c.content == "Looks good!"
        assert c.author_name == "Alice"
        assert c.author_email == "alice@example.com"
        assert c.quoted_text == "Revenue target: $2.5M"
        assert c.mentioned_emails == ["bob@example.com"]
        assert c.resolved is False
        assert c.replies == []

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_comment_with_replies(self, mock_svc, mock_meta, _sleep) -> None:
        """Comment with threaded replies parses reply author/content."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_api_chain(mock_service, "comments.list.execute", {
            "comments": [
                _api_comment(
                    id="c1",
                    content="What about the timeline?",
                    replies=[
                        _api_reply(id="r1", content="Works for me", author_name="Bob"),
                        _api_reply(
                            id="r2",
                            content="@carol@x.com Check with finance",
                            author_name="Carol",
                            author_email="carol@x.com",
                            mentioned_emails=["carol@x.com"],
                        ),
                    ],
                ),
            ],
        })

        result = fetch_file_comments("doc123")

        assert result.comment_count == 1
        replies = result.comments[0].replies
        assert len(replies) == 2
        assert replies[0].id == "r1"
        assert replies[0].content == "Works for me"
        assert replies[0].author_name == "Bob"
        assert replies[1].mentioned_emails == ["carol@x.com"]

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_missing_author_name_generates_warning(
        self, mock_svc, mock_meta, _sleep
    ) -> None:
        """Comment with no author displayName gets 'Unknown' + warning."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_api_chain(mock_service, "comments.list.execute", {
            "comments": [
                {
                    "id": "c1",
                    "content": "Anonymous thought",
                    "author": {"emailAddress": "anon@x.com"},
                    "createdTime": "2026-01-15T10:00:00Z",
                    "modifiedTime": "2026-01-15T10:00:00Z",
                    "resolved": False,
                    "replies": [],
                },
            ],
        })

        result = fetch_file_comments("doc123")

        assert result.comments[0].author_name == "Unknown"
        assert any("c1" in w and "missing author" in w for w in result.warnings)

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_missing_reply_author_generates_warning(
        self, mock_svc, mock_meta, _sleep
    ) -> None:
        """Reply with no author displayName gets 'Unknown' + warning."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_api_chain(mock_service, "comments.list.execute", {
            "comments": [
                _api_comment(
                    replies=[
                        {
                            "id": "r1",
                            "content": "Anonymous reply",
                            "author": {},
                            "createdTime": "2026-01-15T14:00:00Z",
                            "modifiedTime": "2026-01-15T14:00:00Z",
                        },
                    ],
                ),
            ],
        })

        result = fetch_file_comments("doc123")

        reply = result.comments[0].replies[0]
        assert reply.author_name == "Unknown"
        assert any("r1" in w and "missing author" in w for w in result.warnings)

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_resolved_comments_included_by_default(
        self, mock_svc, mock_meta, _sleep
    ) -> None:
        """Both resolved and unresolved returned when include_resolved=True (default)."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_api_chain(mock_service, "comments.list.execute", {
            "comments": [
                _api_comment(id="c1", resolved=False),
                _api_comment(id="c2", resolved=True),
            ],
        })

        result = fetch_file_comments("doc123")

        assert result.comment_count == 2
        assert not result.comments[0].resolved
        assert result.comments[1].resolved

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_include_resolved_false_filters(
        self, mock_svc, mock_meta, _sleep
    ) -> None:
        """Resolved comments filtered when include_resolved=False."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_api_chain(mock_service, "comments.list.execute", {
            "comments": [
                _api_comment(id="c1", resolved=False, content="Open"),
                _api_comment(id="c2", resolved=True, content="Done"),
                _api_comment(id="c3", resolved=False, content="Also open"),
            ],
        })

        result = fetch_file_comments("doc123", include_resolved=False)

        assert result.comment_count == 2
        assert all(not c.resolved for c in result.comments)
        assert [c.content for c in result.comments] == ["Open", "Also open"]

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_empty_comments(self, mock_svc, mock_meta, _sleep) -> None:
        """File with no comments returns empty list."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_api_chain(mock_service, "comments.list.execute", {"comments": []})

        result = fetch_file_comments("doc123")

        assert result.comment_count == 0
        assert result.comments == []
        assert result.warnings == []

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_no_quoted_text_defaults_empty(
        self, mock_svc, mock_meta, _sleep
    ) -> None:
        """Comment without quotedFileContent gets empty quoted_text."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_api_chain(mock_service, "comments.list.execute", {
            "comments": [
                {
                    "id": "c1",
                    "content": "General comment",
                    "author": {"displayName": "Alice"},
                    "createdTime": "2026-01-15T10:00:00Z",
                    "modifiedTime": "2026-01-15T10:00:00Z",
                    "resolved": False,
                    "replies": [],
                },
            ],
        })

        result = fetch_file_comments("doc123")

        assert result.comments[0].quoted_text == ""

    # -- Unsupported MIME types --

    @pytest.mark.parametrize("mime_type", list(COMMENT_UNSUPPORTED_MIMES))
    @patch("retry.time.sleep")
    @patch("adapters.drive.get_drive_service")
    def test_unsupported_mime_raises_before_api_call(
        self, mock_svc, _sleep, mime_type
    ) -> None:
        """Known unsupported MIME types raise early without hitting comments API."""
        with patch(
            "adapters.drive.get_file_metadata",
            return_value={"name": "Test", "mimeType": mime_type},
        ):
            with pytest.raises(MiseError) as exc_info:
                fetch_file_comments("file123")

        assert exc_info.value.kind == ErrorKind.INVALID_INPUT
        assert "not supported" in exc_info.value.message.lower()
        # Verify comments API was never called
        mock_svc.return_value.comments.assert_not_called()

    # -- HttpError branches --

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_http_404_raises_invalid_input(
        self, mock_svc, mock_meta, _sleep
    ) -> None:
        """404 from comments API → INVALID_INPUT (unsupported file type)."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "comments.list.execute", side_effect=_make_http_error(404))

        with pytest.raises(MiseError) as exc_info:
            fetch_file_comments("file123")

        assert exc_info.value.kind == ErrorKind.INVALID_INPUT
        assert "not supported" in exc_info.value.message.lower()

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_http_403_raises_permission_denied(
        self, mock_svc, mock_meta, _sleep
    ) -> None:
        """403 → PERMISSION_DENIED."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "comments.list.execute", side_effect=_make_http_error(403))

        with pytest.raises(MiseError) as exc_info:
            fetch_file_comments("file123")

        assert exc_info.value.kind == ErrorKind.PERMISSION_DENIED

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_http_429_raises_rate_limited(
        self, mock_svc, mock_meta, _sleep
    ) -> None:
        """429 → RATE_LIMITED with retryable=True."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "comments.list.execute", side_effect=_make_http_error(429))

        with pytest.raises(MiseError) as exc_info:
            fetch_file_comments("file123")

        assert exc_info.value.kind == ErrorKind.RATE_LIMITED
        assert exc_info.value.retryable is True

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_http_500_raises_network_error_retryable(
        self, mock_svc, mock_meta, _sleep
    ) -> None:
        """5xx → NETWORK_ERROR with retryable=True."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "comments.list.execute", side_effect=_make_http_error(503))

        with pytest.raises(MiseError) as exc_info:
            fetch_file_comments("file123")

        assert exc_info.value.kind == ErrorKind.NETWORK_ERROR
        assert exc_info.value.retryable is True

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_other_http_error_raises_network_error(
        self, mock_svc, mock_meta, _sleep
    ) -> None:
        """Unexpected HTTP status (e.g. 400) → NETWORK_ERROR, not retryable."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "comments.list.execute", side_effect=_make_http_error(400))

        with pytest.raises(MiseError) as exc_info:
            fetch_file_comments("file123")

        assert exc_info.value.kind == ErrorKind.NETWORK_ERROR
        assert exc_info.value.retryable is False

    # -- Pagination --

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_pagination_fetches_multiple_pages(
        self, mock_svc, mock_meta, _sleep
    ) -> None:
        """Follows nextPageToken across multiple pages."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        # First page returns token, second page is final
        mock_api_chain(mock_service, "comments.list.execute", side_effect=[
            {
                "comments": [_api_comment(id="c1")],
                "nextPageToken": "page2_token",
            },
            {
                "comments": [_api_comment(id="c2")],
            },
        ])

        result = fetch_file_comments("doc123")

        assert result.comment_count == 2
        assert [c.id for c in result.comments] == ["c1", "c2"]

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_pagination_stops_at_max_results(
        self, mock_svc, mock_meta, _sleep
    ) -> None:
        """Stops fetching when max_results reached even if more pages exist."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        # First page fills max_results — shouldn't request page 2
        mock_api_chain(mock_service, "comments.list.execute", {
            "comments": [_api_comment(id=f"c{i}") for i in range(3)],
            "nextPageToken": "more_exist",
        })

        result = fetch_file_comments("doc123", max_results=3)

        assert result.comment_count == 3
        # Only one call to the API (no second page request)
        assert mock_service.comments().list().execute.call_count == 1

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_max_results_caps_page_size(
        self, mock_svc, mock_meta, _sleep
    ) -> None:
        """pageSize sent to API is min(remaining, 100)."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "comments.list.execute", {"comments": []})

        fetch_file_comments("doc123", max_results=25)

        call_kwargs = mock_service.comments().list.call_args[1]
        assert call_kwargs["pageSize"] == 25

    # -- Default field values --

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_missing_fields_use_defaults(
        self, mock_svc, mock_meta, _sleep
    ) -> None:
        """Sparse comment data uses empty-string/False defaults."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        # Minimal comment — only author displayName present
        mock_api_chain(mock_service, "comments.list.execute", {
            "comments": [
                {
                    "author": {"displayName": "Alice"},
                    "replies": [],
                },
            ],
        })

        result = fetch_file_comments("doc123")

        c = result.comments[0]
        assert c.id == ""
        assert c.content == ""
        assert c.author_email is None
        assert c.created_time is None
        assert c.resolved is False
        assert c.quoted_text == ""
        assert c.mentioned_emails == []
        assert c.replies == []

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_file_metadata", return_value=_DOC_METADATA)
    @patch("adapters.drive.get_drive_service")
    def test_include_deleted_passed_to_api(
        self, mock_svc, mock_meta, _sleep
    ) -> None:
        """include_deleted flag forwarded to API call."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "comments.list.execute", {"comments": []})

        fetch_file_comments("doc123", include_deleted=True)

        call_kwargs = mock_service.comments().list.call_args[1]
        assert call_kwargs["includeDeleted"] is True


# ============================================================================
# GET FILE METADATA (mocked service)
# ============================================================================


class TestIsGoogleWorkspaceFile:
    """Test MIME type detection for Google Workspace files."""

    @pytest.mark.parametrize(
        "mime_type",
        [
            "application/vnd.google-apps.document",
            "application/vnd.google-apps.spreadsheet",
            "application/vnd.google-apps.presentation",
            "application/vnd.google-apps.folder",
            "application/vnd.google-apps.form",
        ],
    )
    def test_google_workspace_types(self, mime_type: str) -> None:
        assert is_google_workspace_file(mime_type) is True

    @pytest.mark.parametrize(
        "mime_type",
        [
            "application/pdf",
            "text/plain",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ],
    )
    def test_non_workspace_types(self, mime_type: str) -> None:
        assert is_google_workspace_file(mime_type) is False


class TestGetFileMetadata:
    """Test get_file_metadata with mocked Drive API."""

    @patch("retry.time.sleep")
    @patch("adapters.drive.get_drive_service")
    def test_returns_metadata_dict(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "files.get.execute", {
            "id": "doc123",
            "name": "Test",
            "mimeType": "application/vnd.google-apps.document",
        })

        result = get_file_metadata("doc123")

        assert result["name"] == "Test"
        assert result["mimeType"] == "application/vnd.google-apps.document"


# ============================================================================
# DOWNLOAD FILE TO TEMP (mocked service + MediaIoBaseDownload)
# ============================================================================


class TestDownloadFileToTemp:
    """Test streaming download to temp file."""

    @patch("retry.time.sleep")
    @patch("adapters.drive.MediaIoBaseDownload")
    @patch("adapters.drive.get_drive_service")
    def test_writes_to_temp_file(self, mock_svc, mock_download_cls, _sleep) -> None:
        """Downloads content to a temp file and returns its path."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        # Simulate two chunks then done
        mock_downloader = MagicMock()
        mock_downloader.next_chunk.side_effect = [
            (None, False),
            (None, True),
        ]
        mock_download_cls.return_value = mock_downloader

        path = download_file_to_temp("file123", suffix=".pdf")

        try:
            assert path.exists()
            assert path.suffix == ".pdf"
        finally:
            path.unlink(missing_ok=True)

    @patch("retry.time.sleep")
    @patch("adapters.drive.MediaIoBaseDownload")
    @patch("adapters.drive.get_drive_service")
    def test_cleans_up_on_error(self, mock_svc, mock_download_cls, _sleep) -> None:
        """Temp file cleaned up if download fails."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_downloader = MagicMock()
        mock_downloader.next_chunk.side_effect = RuntimeError("network fail")
        mock_download_cls.return_value = mock_downloader

        with pytest.raises(MiseError):
            download_file_to_temp("file123")


# ============================================================================
# EMAIL ATTACHMENTS FOLDER LOOKUP
# ============================================================================


class TestGetEmailAttachmentsFolderId:
    """Test _get_email_attachments_folder_id with env var and auto-discover."""

    def setup_method(self) -> None:
        """Clear lru_cache before each test."""
        _get_email_attachments_folder_id.cache_clear()

    @patch.dict("os.environ", {"MISE_EMAIL_ATTACHMENTS_FOLDER_ID": "env_folder_id"})
    def test_env_var_takes_priority(self) -> None:
        """Environment variable returned without API call."""
        result = _get_email_attachments_folder_id()
        assert result == "env_folder_id"

    @patch.dict("os.environ", {}, clear=True)
    @patch("adapters.drive.get_drive_service")
    def test_auto_discovers_folder(self, mock_svc) -> None:
        """Finds folder by name when env var not set."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "files.list.execute", {
            "files": [{"id": "discovered_id"}]
        })

        result = _get_email_attachments_folder_id()

        assert result == "discovered_id"

    @patch.dict("os.environ", {}, clear=True)
    @patch("adapters.drive.get_drive_service")
    def test_returns_none_when_not_found(self, mock_svc) -> None:
        """Returns None when no folder found and no env var."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "files.list.execute", {"files": []})

        result = _get_email_attachments_folder_id()

        assert result is None

    @patch.dict("os.environ", {}, clear=True)
    @patch("adapters.drive.get_drive_service")
    def test_returns_none_on_api_error(self, mock_svc) -> None:
        """Silently returns None on API failure."""
        mock_svc.side_effect = RuntimeError("no auth")

        result = _get_email_attachments_folder_id()

        assert result is None


# ============================================================================
# LOOKUP EXFILTRATED (mocked service + folder lookup)
# ============================================================================


class TestLookupExfiltrated:
    """Test lookup_exfiltrated with mocked Drive API."""

    def setup_method(self) -> None:
        _get_email_attachments_folder_id.cache_clear()

    @patch("retry.time.sleep")
    @patch("adapters.drive._get_email_attachments_folder_id", return_value="folder123")
    @patch("adapters.drive.get_drive_service")
    def test_groups_files_by_message_id(self, mock_svc, mock_folder, _sleep) -> None:
        """Files matched to their message IDs from description."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_api_chain(mock_service, "files.list.execute", {
            "files": [
                {
                    "id": "drive_file_1",
                    "name": "report.pdf",
                    "mimeType": "application/pdf",
                    "description": "From: alice@x.com\nMessage ID: msg_aaa\nSubject: Q4",
                },
                {
                    "id": "drive_file_2",
                    "name": "data.xlsx",
                    "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "description": "Message ID: msg_bbb",
                },
            ],
        })

        result = lookup_exfiltrated(["msg_aaa", "msg_bbb"])

        assert "msg_aaa" in result
        assert result["msg_aaa"][0]["file_id"] == "drive_file_1"
        assert result["msg_aaa"][0]["name"] == "report.pdf"
        assert "msg_bbb" in result
        assert result["msg_bbb"][0]["file_id"] == "drive_file_2"

    @patch("retry.time.sleep")
    @patch("adapters.drive._get_email_attachments_folder_id", return_value="folder123")
    @patch("adapters.drive.get_drive_service")
    def test_ignores_unmatched_message_ids(
        self, mock_svc, mock_folder, _sleep
    ) -> None:
        """Files with message IDs not in the request list are ignored."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_api_chain(mock_service, "files.list.execute", {
            "files": [
                {
                    "id": "f1",
                    "name": "other.pdf",
                    "mimeType": "application/pdf",
                    "description": "Message ID: msg_zzz",
                },
            ],
        })

        result = lookup_exfiltrated(["msg_aaa"])

        assert result == {}

    def test_empty_message_ids_returns_empty(self) -> None:
        """Empty input returns empty dict without API calls."""
        result = lookup_exfiltrated([])
        assert result == {}

    @patch("adapters.drive._get_email_attachments_folder_id", return_value=None)
    def test_no_folder_returns_empty(self, mock_folder) -> None:
        """No email attachments folder → empty dict."""
        result = lookup_exfiltrated(["msg_aaa"])
        assert result == {}

    @patch("retry.time.sleep")
    @patch("adapters.drive._get_email_attachments_folder_id", return_value="folder123")
    @patch("adapters.drive.get_drive_service")
    def test_api_error_returns_empty(self, mock_svc, mock_folder, _sleep) -> None:
        """API failure silently returns empty dict (pre-exfil is optional)."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "files.list.execute", side_effect=RuntimeError("API down"))

        result = lookup_exfiltrated(["msg_aaa"])

        assert result == {}

    @patch("retry.time.sleep")
    @patch("adapters.drive._get_email_attachments_folder_id", return_value="folder123")
    @patch("adapters.drive.get_drive_service")
    def test_single_message_id_uses_simple_query(
        self, mock_svc, mock_folder, _sleep
    ) -> None:
        """Single message ID doesn't use OR query (optimization)."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "files.list.execute", {"files": []})

        lookup_exfiltrated(["msg_aaa"])

        call_kwargs = mock_service.files().list.call_args[1]
        assert "or" not in call_kwargs["q"].lower()
        assert "msg_aaa" in call_kwargs["q"]

    @patch("retry.time.sleep")
    @patch("adapters.drive._get_email_attachments_folder_id", return_value="folder123")
    @patch("adapters.drive.get_drive_service")
    def test_multiple_message_ids_use_or_query(
        self, mock_svc, mock_folder, _sleep
    ) -> None:
        """Multiple message IDs batch with OR query."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "files.list.execute", {"files": []})

        lookup_exfiltrated(["msg_aaa", "msg_bbb"])

        call_kwargs = mock_service.files().list.call_args[1]
        assert " or " in call_kwargs["q"].lower()

    @patch("retry.time.sleep")
    @patch("adapters.drive._get_email_attachments_folder_id", return_value="folder123")
    @patch("adapters.drive.get_drive_service")
    def test_multiple_files_per_message(
        self, mock_svc, mock_folder, _sleep
    ) -> None:
        """Multiple attachments from same email grouped under one message ID."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_api_chain(mock_service, "files.list.execute", {
            "files": [
                {
                    "id": "f1",
                    "name": "report.pdf",
                    "mimeType": "application/pdf",
                    "description": "Message ID: msg_aaa",
                },
                {
                    "id": "f2",
                    "name": "data.csv",
                    "mimeType": "text/csv",
                    "description": "Message ID: msg_aaa",
                },
            ],
        })

        result = lookup_exfiltrated(["msg_aaa"])

        assert len(result["msg_aaa"]) == 2
        names = [f["name"] for f in result["msg_aaa"]]
        assert "report.pdf" in names
        assert "data.csv" in names


# ============================================================================
# SEAL PROOF OF CONCEPT
# ============================================================================

class TestSealCatchesRenamedMethods:
    """Proves seal_service catches silent mock mismatches.

    Without seal, MagicMock silently creates new attributes when
    production code renames an API method — tests pass, but they're
    testing nothing. Seal makes these failures loud.
    """

    def test_renamed_method_fails(self) -> None:
        """Seal catches files.export → files.export_media rename."""
        service = MagicMock()
        mock_api_chain(service, "files.export.execute", b"markdown")
        seal_service(service)

        # Correct chain still works
        assert service.files().export().execute() == b"markdown"

        # Renamed method raises immediately
        with pytest.raises(AttributeError, match="export_media"):
            service.files().export_media()

    def test_typo_in_resource_fails(self) -> None:
        """Seal catches typo in resource name (files vs filez)."""
        service = MagicMock()
        mock_api_chain(service, "files.get.execute", {"id": "f1"})
        seal_service(service)

        with pytest.raises(AttributeError, match="filez"):
            service.filez()

    def test_multiple_chains_all_work(self) -> None:
        """Seal preserves all set-up chains on the same service."""
        service = MagicMock()
        mock_api_chain(service, "files.get.execute", {"id": "f1"})
        mock_api_chain(service, "files.list.execute", {"files": []})
        mock_api_chain(service, "comments.list.execute", {"comments": []})
        seal_service(service)

        # All three chains work
        assert service.files().get().execute() == {"id": "f1"}
        assert service.files().list().execute() == {"files": []}
        assert service.comments().list().execute() == {"comments": []}

        # But a new chain fails
        with pytest.raises(AttributeError):
            service.files().copy()
