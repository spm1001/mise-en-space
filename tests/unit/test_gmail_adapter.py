"""
Tests for gmail adapter pure helpers and fetch wiring.

Tests the helper functions that parse API response data,
and the adapter functions with mocked Gmail service.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from models import GmailThreadData, EmailMessage
from adapters.gmail import (
    _parse_headers,
    _parse_address_list,
    _parse_date,
    _extract_drive_links,
    _build_message,
    fetch_thread,
)


FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"


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

    def test_non_google_link_ignored(self) -> None:
        text = "See https://example.com/document"
        assert _extract_drive_links(text) == []


# ============================================================================
# BUILD MESSAGE (mocked from fixture data)
# ============================================================================

class TestBuildMessage:
    """Test message construction from API response."""

    def test_from_real_fixture(self) -> None:
        """Build message from real Gmail thread fixture."""
        fixture = json.loads((FIXTURES_DIR / "gmail" / "real_thread.json").read_text())
        msg = fixture["messages"][0]

        result = _build_message(msg)

        assert isinstance(result, EmailMessage)
        assert result.message_id == msg["id"]
        assert result.subject != ""  # Has a subject
        assert result.from_address != ""  # Has a sender

    def test_minimal_message(self) -> None:
        """Message with minimal fields doesn't crash."""
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
        fixture = json.loads((FIXTURES_DIR / "gmail" / "real_thread.json").read_text())

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.users().threads().get().execute.return_value = fixture

        with patch('retry.time.sleep'):
            result = fetch_thread("19beb7eba557288e")

        assert isinstance(result, GmailThreadData)
        assert result.thread_id == fixture["id"]
        assert len(result.messages) == len(fixture["messages"])

    @patch('adapters.gmail.get_gmail_service')
    def test_messages_parsed_from_fixture(self, mock_get_service) -> None:
        """Each message in thread is parsed into EmailMessage."""
        fixture = json.loads((FIXTURES_DIR / "gmail" / "real_thread.json").read_text())

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.users().threads().get().execute.return_value = fixture

        with patch('retry.time.sleep'):
            result = fetch_thread("19beb7eba557288e")

        for msg in result.messages:
            assert isinstance(msg, EmailMessage)
            assert msg.message_id != ""
