"""
Tests for gmail adapter pure helpers and fetch wiring.

Tests the helper functions that parse API response data,
and the adapter functions with mocked Gmail service.
"""

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from models import GmailThreadData, GmailSearchResult, EmailMessage
from tests.helpers import mock_api_chain
from tests.conftest import load_fixture
from adapters.gmail import (
    _parse_headers,
    _parse_address_list,
    _parse_date,
    _extract_drive_links,
    _build_message,
    fetch_thread,
    fetch_message,
    search_threads,
    download_attachment,
    AttachmentDownload,
    DRIVE_LINK_PATTERN,
)



# ============================================================================
# PURE HELPERS
# ============================================================================

class TestParseHeaders:
    """Test header extraction from API payload."""

    def test_extracts_wanted_headers(self) -> None:
        headers = [
            {"name": "From", "value": "alice@example.com"},
            {"name": "To", "value": "bob@example.com"},
            {"name": "Subject", "value": "Test"},
            {"name": "Date", "value": "Mon, 1 Jan 2026 10:00:00 +0000"},
            {"name": "X-Mailer", "value": "Thunderbird"},  # not wanted
        ]
        result = _parse_headers(headers)

        assert result["From"] == "alice@example.com"
        assert result["To"] == "bob@example.com"
        assert result["Subject"] == "Test"
        assert result["Date"] == "Mon, 1 Jan 2026 10:00:00 +0000"
        assert "X-Mailer" not in result

    def test_empty_headers(self) -> None:
        assert _parse_headers([]) == {}

    def test_missing_name_field_skipped(self) -> None:
        headers = [{"value": "orphan"}]
        assert _parse_headers(headers) == {}


class TestParseAddressList:
    """Test comma-separated email address parsing."""

    def test_single_address(self) -> None:
        assert _parse_address_list("alice@example.com") == ["alice@example.com"]

    def test_multiple_addresses(self) -> None:
        result = _parse_address_list("alice@example.com, bob@example.com")
        assert result == ["alice@example.com", "bob@example.com"]

    def test_addresses_with_whitespace(self) -> None:
        result = _parse_address_list("  alice@example.com ,  bob@example.com  ")
        assert result == ["alice@example.com", "bob@example.com"]

    def test_none_returns_empty(self) -> None:
        assert _parse_address_list(None) == []

    def test_empty_string_returns_empty(self) -> None:
        assert _parse_address_list("") == []

    def test_trailing_comma_no_empty(self) -> None:
        result = _parse_address_list("alice@example.com,")
        assert result == ["alice@example.com"]


class TestParseDate:
    """Test date parsing from header or internal timestamp."""

    def test_rfc2822_header(self) -> None:
        result = _parse_date("Mon, 1 Jan 2026 10:00:00 +0000", None)
        assert result is not None
        assert result.year == 2026
        assert result.month == 1

    def test_internal_date_milliseconds(self) -> None:
        # 1704067200000 = 2024-01-01 00:00:00 UTC
        result = _parse_date(None, "1704067200000")
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_header_preferred_over_internal(self) -> None:
        result = _parse_date("Mon, 1 Jan 2026 10:00:00 +0000", "1704067200000")
        assert result is not None
        assert result.year == 2026  # header wins

    def test_invalid_header_falls_through_to_internal(self) -> None:
        result = _parse_date("not a date", "1704067200000")
        assert result is not None
        assert result.year == 2024  # internal date used

    def test_both_none_returns_none(self) -> None:
        assert _parse_date(None, None) is None

    def test_both_invalid_returns_none(self) -> None:
        assert _parse_date("garbage", "also garbage") is None


class TestExtractDriveLinks:
    """Test Drive link extraction from message text."""

    def test_docs_link(self) -> None:
        text = "Check this doc: https://docs.google.com/document/d/abc123/edit"
        links = _extract_drive_links(text)
        assert len(links) == 1
        assert "docs.google.com" in links[0]["url"]

    def test_sheets_link(self) -> None:
        text = "See https://sheets.google.com/spreadsheets/d/xyz789"
        links = _extract_drive_links(text)
        assert len(links) == 1

    def test_drive_link(self) -> None:
        text = "File: https://drive.google.com/file/d/abc/view"
        links = _extract_drive_links(text)
        assert len(links) == 1

    def test_multiple_links(self) -> None:
        text = (
            "Doc: https://docs.google.com/document/d/1 "
            "Sheet: https://sheets.google.com/spreadsheets/d/2"
        )
        links = _extract_drive_links(text)
        assert len(links) == 2

    def test_no_links(self) -> None:
        assert _extract_drive_links("No links here") == []

    def test_none_input(self) -> None:
        assert _extract_drive_links(None) == []

    def test_slides_link(self) -> None:
        text = "Deck: https://slides.google.com/presentation/d/abc123"
        links = _extract_drive_links(text)
        assert len(links) == 1
        assert "slides.google.com" in links[0]["url"]

    def test_drive_folder_link(self) -> None:
        text = "Folder: https://drive.google.com/drive/folders/abc123"
        links = _extract_drive_links(text)
        assert len(links) == 1
        assert "folders" in links[0]["url"]

    def test_non_google_link_ignored(self) -> None:
        text = "See https://example.com/document"
        assert _extract_drive_links(text) == []


# ============================================================================
# DRIVE_LINK_PATTERN — regex unit tests
# ============================================================================

class TestDriveLinkPattern:
    """
    Direct tests for the DRIVE_LINK_PATTERN regex.

    Covers all four subdomains (docs, sheets, slides, drive), URL shapes,
    terminator behaviour, and case-insensitivity. These tests exercise the
    regex itself — not the _extract_drive_links wrapper — so a future refactor
    of the wrapper can't silently break the pattern.
    """

    # --- subdomains ---

    def test_docs_subdomain_matches(self) -> None:
        url = "https://docs.google.com/document/d/abc123/edit"
        assert DRIVE_LINK_PATTERN.search(url) is not None

    def test_sheets_subdomain_matches(self) -> None:
        url = "https://sheets.google.com/spreadsheets/d/xyz789"
        assert DRIVE_LINK_PATTERN.search(url) is not None

    def test_slides_subdomain_matches(self) -> None:
        url = "https://slides.google.com/presentation/d/pqr456"
        assert DRIVE_LINK_PATTERN.search(url) is not None

    def test_drive_subdomain_matches(self) -> None:
        url = "https://drive.google.com/file/d/abc/view"
        assert DRIVE_LINK_PATTERN.search(url) is not None

    def test_unknown_subdomain_does_not_match(self) -> None:
        url = "https://mail.google.com/mail/u/0/#inbox"
        assert DRIVE_LINK_PATTERN.search(url) is None

    # --- URL shapes ---

    def test_docs_spreadsheet_url(self) -> None:
        """Sheets opened via docs.google.com (the common real-world form)."""
        url = "https://docs.google.com/spreadsheets/d/abc123/edit#gid=0"
        assert DRIVE_LINK_PATTERN.search(url) is not None

    def test_docs_presentation_url(self) -> None:
        """Slides opened via docs.google.com."""
        url = "https://docs.google.com/presentation/d/abc123/edit"
        assert DRIVE_LINK_PATTERN.search(url) is not None

    def test_drive_folder_url(self) -> None:
        url = "https://drive.google.com/drive/folders/abc123?usp=sharing"
        assert DRIVE_LINK_PATTERN.search(url) is not None

    def test_query_params_included_in_match(self) -> None:
        """Query params are part of the URL and captured."""
        url = "https://docs.google.com/document/d/abc/edit?usp=sharing"
        m = DRIVE_LINK_PATTERN.search(url)
        assert m is not None
        assert "usp=sharing" in m.group(0)

    # --- terminator behaviour ---

    def test_stops_at_double_quote(self) -> None:
        """URL inside HTML href terminates at the closing quote."""
        html = 'href="https://docs.google.com/document/d/abc/edit" class="link"'
        m = DRIVE_LINK_PATTERN.search(html)
        assert m is not None
        assert m.group(0) == "https://docs.google.com/document/d/abc/edit"

    def test_stops_at_single_quote(self) -> None:
        html = "href='https://docs.google.com/document/d/abc/edit' class='link'"
        m = DRIVE_LINK_PATTERN.search(html)
        assert m is not None
        assert m.group(0) == "https://docs.google.com/document/d/abc/edit"

    def test_stops_at_angle_bracket(self) -> None:
        """URL in plain-text email followed by >."""
        text = "See <https://docs.google.com/document/d/abc/edit> for details."
        m = DRIVE_LINK_PATTERN.search(text)
        assert m is not None
        assert m.group(0) == "https://docs.google.com/document/d/abc/edit"

    def test_stops_at_whitespace(self) -> None:
        text = "Doc: https://docs.google.com/document/d/abc/edit and more."
        m = DRIVE_LINK_PATTERN.search(text)
        assert m is not None
        assert m.group(0) == "https://docs.google.com/document/d/abc/edit"

    # --- case-insensitivity ---

    def test_uppercase_scheme_matches(self) -> None:
        url = "HTTPS://DOCS.GOOGLE.COM/document/d/abc123/edit"
        assert DRIVE_LINK_PATTERN.search(url) is not None

    # --- non-matches ---

    def test_non_google_domain_ignored(self) -> None:
        assert DRIVE_LINK_PATTERN.search("https://example.com/docs/file") is None

    def test_plain_text_no_urls_ignored(self) -> None:
        assert DRIVE_LINK_PATTERN.search("No links in this message.") is None

    def test_findall_returns_all_urls(self) -> None:
        """Multiple Drive URLs in one string all captured."""
        text = (
            "Doc: https://docs.google.com/document/d/1/edit "
            "Folder: https://drive.google.com/drive/folders/abc "
            "Deck: https://slides.google.com/presentation/d/xyz"
        )
        matches = DRIVE_LINK_PATTERN.findall(text)
        assert len(matches) == 3
        assert any("document" in m for m in matches)
        assert any("folders" in m for m in matches)
        assert any("presentation" in m for m in matches)


# ============================================================================
# BUILD MESSAGE (mocked from fixture data)
# ============================================================================

class TestBuildMessage:
    """Test message construction from API response."""

    def test_from_real_fixture(self) -> None:
        """Build message from real Gmail thread fixture — verifies body decoding."""
        fixture = load_fixture("gmail", "real_thread")
        msg = fixture["messages"][0]

        result = _build_message(msg)

        assert isinstance(result, EmailMessage)
        assert result.message_id == msg["id"]
        assert result.subject == "Test email"
        assert "bob@example.com" in result.from_address

        # Bodies decoded from base64 payload
        assert result.body_text is not None
        assert "This is some text" in result.body_text
        assert "Bullet 1" in result.body_text
        assert result.body_html is not None
        assert "This is some text" in result.body_html

    def test_from_real_fixture_second_message(self) -> None:
        """Build reply message — has both text/html and quoted thread."""
        fixture = load_fixture("gmail", "real_thread")
        msg = fixture["messages"][1]

        result = _build_message(msg)

        assert result.subject == "Re: Test email"
        assert result.body_text is not None
        assert "building on the thread" in result.body_text
        assert result.body_html is not None
        assert "building on the thread" in result.body_html

    def test_from_real_fixture_attachments(self) -> None:
        """Real fixture message has attachment metadata."""
        fixture = load_fixture("gmail", "real_thread")
        msg = fixture["messages"][0]

        result = _build_message(msg)

        # Message 1 has a PDF attachment
        pdf_attachments = [a for a in result.attachments if a.mime_type == "application/pdf"]
        assert len(pdf_attachments) == 1
        assert "Consulting proposal" in pdf_attachments[0].filename

    def test_from_real_fixture_drive_links(self) -> None:
        """Real fixture messages contain Drive links."""
        fixture = load_fixture("gmail", "real_thread")

        msg1 = _build_message(fixture["messages"][0])
        assert len(msg1.drive_links) > 0  # Has Test Single Tab Document link

        msg2 = _build_message(fixture["messages"][1])
        assert len(msg2.drive_links) > 0  # Has Pet resume link

    def test_minimal_message(self) -> None:
        """Message with minimal fields — body decoded from base64."""
        msg = {
            "id": "msg123",
            "payload": {
                "headers": [
                    {"name": "From", "value": "test@example.com"},
                    {"name": "Subject", "value": "Minimal"},
                ],
                "mimeType": "text/plain",
                "body": {"data": "SGVsbG8="},  # base64 "Hello"
            },
        }
        result = _build_message(msg)

        assert result.message_id == "msg123"
        assert result.from_address == "test@example.com"
        assert result.subject == "Minimal"
        assert result.body_text == "Hello"

    def test_message_without_headers(self) -> None:
        """Message with no headers gets empty defaults."""
        msg = {"id": "bare", "payload": {"mimeType": "text/plain", "body": {}}}
        result = _build_message(msg)
        assert result.from_address == ""
        assert result.subject == ""


# ============================================================================
# FETCH THREAD (mocked service, real fixture)
# ============================================================================

class TestFetchThread:
    """Test fetch_thread wiring with mocked Gmail API."""

    @patch('adapters.gmail.get_gmail_service')
    def test_returns_thread_data(self, mock_get_service) -> None:
        """fetch_thread returns GmailThreadData from API response."""
        fixture = load_fixture("gmail", "real_thread")

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_api_chain(mock_service, "users.threads.get.execute", fixture)

        with patch('retry.time.sleep'):
            result = fetch_thread("19beb7eba557288e")

        assert isinstance(result, GmailThreadData)
        assert result.thread_id == fixture["id"]
        assert len(result.messages) == len(fixture["messages"])

    @patch('adapters.gmail.get_gmail_service')
    def test_messages_parsed_from_fixture(self, mock_get_service) -> None:
        """Each message in thread is parsed with decoded bodies."""
        fixture = load_fixture("gmail", "real_thread")

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_api_chain(mock_service, "users.threads.get.execute", fixture)

        with patch('retry.time.sleep'):
            result = fetch_thread("19beb7eba557288e")

        for msg in result.messages:
            assert isinstance(msg, EmailMessage)
            assert msg.message_id != ""
            # Round-trip: API payload → adapter → decoded bodies
            assert msg.body_text is not None, f"Message {msg.message_id} has no body_text"
            assert msg.body_html is not None, f"Message {msg.message_id} has no body_html"

        # Verify specific content survived the round-trip
        assert "This is some text" in result.messages[0].body_text
        assert "building on the thread" in result.messages[1].body_text


# ============================================================================
# FETCH MESSAGE (mocked service)
# ============================================================================

class TestFetchMessage:
    """Test fetch_message wiring."""

    @patch('adapters.gmail.get_gmail_service')
    def test_returns_email_message(self, mock_get_service) -> None:
        """fetch_message returns parsed EmailMessage."""
        fixture = load_fixture("gmail", "real_thread")
        msg_data = fixture["messages"][0]

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_api_chain(mock_service, "users.messages.get.execute", msg_data)

        with patch('retry.time.sleep'):
            result = fetch_message(msg_data["id"])

        assert isinstance(result, EmailMessage)
        assert result.message_id == msg_data["id"]


# ============================================================================
# SEARCH THREADS (mocked service with batch callback)
# ============================================================================

class TestSearchThreads:
    """Test search_threads with mocked batch API."""

    @patch('adapters.gmail.get_gmail_service')
    def test_empty_search_returns_empty(self, mock_get_service) -> None:
        """No matching threads returns empty list."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_api_chain(mock_service, "users.threads.list.execute", {
            "threads": [],
            "resultSizeEstimate": 0,
        })

        with patch('retry.time.sleep'):
            result = search_threads("nonexistent query")

        assert result == []

    @patch('adapters.gmail.get_gmail_service')
    def test_no_threads_key_returns_empty(self, mock_get_service) -> None:
        """Response without threads key returns empty list."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_api_chain(mock_service, "users.threads.list.execute", {})

        with patch('retry.time.sleep'):
            result = search_threads("test")

        assert result == []

    @patch('adapters.gmail.get_gmail_service')
    def test_search_with_results(self, mock_get_service) -> None:
        """Search with results triggers batch fetch and returns GmailSearchResults."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        # Step 1: threads().list() returns thread IDs
        mock_api_chain(mock_service, "users.threads.list.execute", {
            "threads": [
                {"id": "t1", "snippet": "Budget discussion"},
                {"id": "t2", "snippet": "Q4 results"},
            ],
        })

        # Step 2: batch.execute() calls the callbacks
        # We need to capture the callback and invoke it manually
        batch_mock = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_mock

        callbacks = []

        def capture_add(request, callback):
            callbacks.append(callback)

        batch_mock.add.side_effect = capture_add

        def execute_batch():
            # Simulate batch responses
            callbacks[0]("0", {
                "id": "t1",
                "messages": [{
                    "id": "m1",
                    "internalDate": "1706745600000",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "alice@example.com"},
                            {"name": "Subject", "value": "Budget"},
                        ],
                        "mimeType": "text/plain",
                    },
                }],
            }, None)
            callbacks[1]("1", {
                "id": "t2",
                "messages": [{
                    "id": "m2",
                    "internalDate": "1706832000000",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "bob@example.com"},
                            {"name": "Subject", "value": "Q4"},
                        ],
                        "mimeType": "text/plain",
                    },
                }],
            }, None)

        batch_mock.execute.side_effect = execute_batch

        with patch('retry.time.sleep'):
            results = search_threads("budget", max_results=10)

        assert len(results) == 2
        assert all(isinstance(r, GmailSearchResult) for r in results)
        assert results[0].thread_id == "t1"
        assert results[0].subject == "Budget"
        assert results[0].snippet == "Budget discussion"
        assert results[1].thread_id == "t2"

    @patch('adapters.gmail.get_gmail_service')
    def test_results_preserve_relevance_order(self, mock_get_service) -> None:
        """Results arrive in threads().list() order, not batch callback order."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        # threads().list() returns t1, t2, t3 in relevance order
        mock_api_chain(mock_service, "users.threads.list.execute", {
            "threads": [
                {"id": "t1", "snippet": "Most relevant"},
                {"id": "t2", "snippet": "Second"},
                {"id": "t3", "snippet": "Third"},
            ],
        })

        batch_mock = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_mock

        callbacks = []
        batch_mock.add.side_effect = lambda req, callback: callbacks.append(callback)

        def execute_batch():
            # Simulate callbacks in REVERSE order (t3, t1, t2)
            for tid, subj in [("t3", "Third"), ("t1", "First"), ("t2", "Second")]:
                callbacks[{"t1": 0, "t2": 1, "t3": 2}[tid]](
                    "0", {
                        "id": tid,
                        "messages": [{
                            "id": f"m-{tid}",
                            "internalDate": "1706745600000",
                            "payload": {
                                "headers": [
                                    {"name": "From", "value": f"{tid}@example.com"},
                                    {"name": "Subject", "value": subj},
                                ],
                                "mimeType": "text/plain",
                            },
                        }],
                    }, None)

        batch_mock.execute.side_effect = execute_batch

        with patch('retry.time.sleep'):
            results = search_threads("test", max_results=10)

        # Must match original relevance order, NOT callback order
        assert len(results) == 3
        assert results[0].thread_id == "t1"
        assert results[1].thread_id == "t2"
        assert results[2].thread_id == "t3"

    @patch('adapters.gmail.get_gmail_service')
    def test_batch_error_skips_thread(self, mock_get_service) -> None:
        """Batch callback errors skip individual threads."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        mock_api_chain(mock_service, "users.threads.list.execute", {
            "threads": [{"id": "t1", "snippet": "test"}],
        })

        batch_mock = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_mock

        callbacks = []
        batch_mock.add.side_effect = lambda req, callback: callbacks.append(callback)

        def execute_batch():
            # Simulate error for this thread
            callbacks[0]("0", None, Exception("API error"))

        batch_mock.execute.side_effect = execute_batch

        with patch('retry.time.sleep'):
            results = search_threads("test")

        assert results == []  # Error thread skipped

    @patch('adapters.gmail.get_gmail_service')
    def test_empty_thread_id_skipped_with_warning(self, mock_get_service, caplog) -> None:
        """Batch response with empty thread_id is skipped, not silently stored."""
        import logging

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        mock_api_chain(mock_service, "users.threads.list.execute", {
            "threads": [{"id": "t1", "snippet": "test"}],
        })

        batch_mock = MagicMock()
        mock_service.new_batch_http_request.return_value = batch_mock

        callbacks = []
        batch_mock.add.side_effect = lambda req, callback: callbacks.append(callback)

        def execute_batch():
            # Simulate response with missing id field
            callbacks[0]("0", {
                "messages": [{
                    "id": "m1",
                    "internalDate": "1706745600000",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "ghost@example.com"},
                            {"name": "Subject", "value": "Phantom"},
                        ],
                        "mimeType": "text/plain",
                    },
                }],
            }, None)

        batch_mock.execute.side_effect = execute_batch

        with patch('retry.time.sleep'), caplog.at_level(logging.WARNING, logger="adapters.gmail"):
            results = search_threads("test")

        assert results == []  # Empty thread_id skipped, not stored under ""
        assert any("empty thread_id" in r.message for r in caplog.records)


# ============================================================================
# DOWNLOAD ATTACHMENT (mocked service)
# ============================================================================

class TestDownloadAttachment:
    """Test attachment download with mocked Gmail API."""

    @patch('adapters.gmail.get_gmail_service')
    def test_small_attachment_in_memory(self, mock_get_service) -> None:
        """Small attachment returns content in memory."""
        import base64
        content = b"Hello, this is a test attachment"
        encoded = base64.urlsafe_b64encode(content).decode()

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_api_chain(mock_service, "users.messages.attachments.get.execute", {
            "data": encoded,
        })

        with patch('retry.time.sleep'):
            result = download_attachment("msg1", "att1", filename="test.txt", mime_type="text/plain")

        assert isinstance(result, AttachmentDownload)
        assert result.content == content
        assert result.filename == "test.txt"
        assert result.mime_type == "text/plain"
        assert result.size == len(content)
        assert result.temp_path is None

    @patch('adapters.gmail.get_gmail_service')
    def test_default_filename_and_mime(self, mock_get_service) -> None:
        """Missing filename/mime get defaults."""
        import base64
        encoded = base64.urlsafe_b64encode(b"data").decode()

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_api_chain(mock_service, "users.messages.attachments.get.execute", {"data": encoded})

        with patch('retry.time.sleep'):
            result = download_attachment("msg1", "att1")

        assert result.filename == "attachment"
        assert result.mime_type == "application/octet-stream"
