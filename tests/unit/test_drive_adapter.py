"""
Tests for drive adapter pure helpers.

Tests _parse_email_context and _parse_datetime which are untested
pure functions in adapters/drive.py.
"""

from datetime import datetime, timezone

from models import EmailContext
from adapters.drive import _parse_email_context, _parse_datetime


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
