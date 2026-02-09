"""
Tests for drive adapter pure helpers.

Tests _parse_email_context and _parse_datetime which are untested
pure functions in adapters/drive.py.
"""

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, Mock

import pytest

from models import EmailContext, DriveSearchResult, MiseError, ErrorKind
from adapters.drive import (
    _parse_email_context,
    _parse_datetime,
    get_file_metadata,
    export_file,
    download_file,
    get_file_size,
    search_files,
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
    """Test export_file with mocked Drive API."""

    @patch('adapters.drive.get_drive_service')
    def test_exports_bytes(self, mock_get_service) -> None:
        """export_file returns bytes from API."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().export().execute.return_value = b"# Exported Markdown"

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
        mock_service.files().get().execute.return_value = {"size": "1024"}
        # Download returns content
        mock_service.files().get_media().execute.return_value = b"file content"

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
        mock_service.files().get().execute.return_value = {"size": str(huge_size)}

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
        mock_service.files().get().execute.return_value = {"size": "12345"}

        result = get_file_size("file123")
        assert result == 12345

    @patch('adapters.drive.get_drive_service')
    def test_missing_size_returns_zero(self, mock_get_service) -> None:
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().get().execute.return_value = {}

        result = get_file_size("folder123")
        assert result == 0


class TestSearchFiles:
    """Test search_files with mocked Drive API."""

    @patch('adapters.drive.get_drive_service')
    def test_returns_search_results(self, mock_get_service) -> None:
        """Search results parsed into DriveSearchResult objects."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        mock_service.files().list().execute.return_value = {
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
        }

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
        mock_service.files().list().execute.return_value = {"files": []}

        with patch('retry.time.sleep'):
            results = search_files("nonexistent")

        assert results == []

    @patch('adapters.drive.get_drive_service')
    def test_exfil_file_includes_email_context(self, mock_get_service) -> None:
        """File with exfil description includes email context."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        mock_service.files().list().execute.return_value = {
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
        }

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
        mock_service.files().list().execute.return_value = {"files": []}

        with patch('retry.time.sleep'):
            search_files("test", max_results=200)

        call_kwargs = mock_service.files().list.call_args[1]
        assert call_kwargs["pageSize"] == 100
