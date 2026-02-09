"""
Tests for retry decorator wiring on adapter functions.

Verifies that adapter functions are actually decorated with @with_retry
and that the wiring works end-to-end (retryable errors trigger retry,
non-retryable errors fail fast with proper MiseError conversion).

This complements test_retry.py which tests the decorator in isolation.
"""

import pytest
from unittest.mock import patch, Mock, MagicMock

from models import MiseError, ErrorKind


# ============================================================================
# DECORATOR PRESENCE VERIFICATION
# ============================================================================

class TestDecoratorPresence:
    """Verify adapter functions are wrapped by @with_retry.

    The decorator wraps functions, changing __wrapped__ attribute.
    This catches the wiring bug where someone forgets the decorator.
    """

    def test_drive_get_file_metadata(self) -> None:
        from adapters.drive import get_file_metadata
        assert hasattr(get_file_metadata, '__wrapped__'), "get_file_metadata missing @with_retry"

    def test_drive_download_file(self) -> None:
        from adapters.drive import download_file
        assert hasattr(download_file, '__wrapped__'), "download_file missing @with_retry"

    def test_drive_export_file(self) -> None:
        from adapters.drive import export_file
        assert hasattr(export_file, '__wrapped__'), "export_file missing @with_retry"

    def test_drive_search_files(self) -> None:
        from adapters.drive import search_files
        assert hasattr(search_files, '__wrapped__'), "search_files missing @with_retry"

    def test_drive_fetch_file_comments(self) -> None:
        from adapters.drive import fetch_file_comments
        assert hasattr(fetch_file_comments, '__wrapped__'), "fetch_file_comments missing @with_retry"

    def test_drive_download_file_to_temp(self) -> None:
        from adapters.drive import download_file_to_temp
        assert hasattr(download_file_to_temp, '__wrapped__'), "download_file_to_temp missing @with_retry"

    def test_drive_lookup_exfiltrated(self) -> None:
        from adapters.drive import lookup_exfiltrated
        assert hasattr(lookup_exfiltrated, '__wrapped__'), "lookup_exfiltrated missing @with_retry"

    def test_gmail_fetch_thread(self) -> None:
        from adapters.gmail import fetch_thread
        assert hasattr(fetch_thread, '__wrapped__'), "fetch_thread missing @with_retry"

    def test_gmail_search_threads(self) -> None:
        from adapters.gmail import search_threads
        assert hasattr(search_threads, '__wrapped__'), "search_threads missing @with_retry"

    def test_gmail_download_attachment(self) -> None:
        from adapters.gmail import download_attachment
        assert hasattr(download_attachment, '__wrapped__'), "download_attachment missing @with_retry"

    def test_gmail_fetch_message(self) -> None:
        from adapters.gmail import fetch_message
        assert hasattr(fetch_message, '__wrapped__'), "fetch_message missing @with_retry"

    def test_docs_fetch_document(self) -> None:
        from adapters.docs import fetch_document
        assert hasattr(fetch_document, '__wrapped__'), "fetch_document missing @with_retry"

    def test_sheets_fetch_spreadsheet(self) -> None:
        from adapters.sheets import fetch_spreadsheet
        assert hasattr(fetch_spreadsheet, '__wrapped__'), "fetch_spreadsheet missing @with_retry"

    def test_slides_fetch_presentation(self) -> None:
        from adapters.slides import fetch_presentation
        assert hasattr(fetch_presentation, '__wrapped__'), "fetch_presentation missing @with_retry"

    def test_conversion_convert_via_drive(self) -> None:
        from adapters.conversion import convert_via_drive
        assert hasattr(convert_via_drive, '__wrapped__'), "convert_via_drive missing @with_retry"

    def test_activity_search_comment_activities(self) -> None:
        from adapters.activity import search_comment_activities
        assert hasattr(search_comment_activities, '__wrapped__'), "search_comment_activities missing @with_retry"

    def test_activity_get_file_activities(self) -> None:
        from adapters.activity import get_file_activities
        assert hasattr(get_file_activities, '__wrapped__'), "get_file_activities missing @with_retry"


# ============================================================================
# END-TO-END WIRING (mocked service, real decorator)
# ============================================================================

class TestRetryWiringEndToEnd:
    """Test that retry actually fires through a real adapter function.

    These mock the Google service but let the real @with_retry decorator run,
    proving the wiring works end-to-end.
    """

    @patch('adapters.drive.get_drive_service')
    def test_drive_retries_on_server_error(self, mock_get_service) -> None:
        """Drive adapter retries on 500, then succeeds."""
        from adapters.drive import get_file_metadata

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        # First call: 500 error. Second call: success.
        error_500 = Exception("Internal error")
        error_500.resp = Mock(status=500)

        mock_service.files().get().execute.side_effect = [
            error_500,
            {"id": "abc", "name": "Test", "mimeType": "text/plain"},
        ]

        with patch('retry.time.sleep'):  # Don't actually wait
            result = get_file_metadata("abc")

        assert result["id"] == "abc"
        assert mock_service.files().get().execute.call_count == 2

    @patch('adapters.drive.get_drive_service')
    def test_drive_fails_fast_on_not_found(self, mock_get_service) -> None:
        """Drive adapter does NOT retry on 404."""
        from adapters.drive import get_file_metadata

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        error_404 = Exception("Not found")
        error_404.resp = Mock(status=404)
        mock_service.files().get().execute.side_effect = error_404

        with pytest.raises(MiseError) as exc_info:
            get_file_metadata("nonexistent")

        assert exc_info.value.kind == ErrorKind.NOT_FOUND
        assert mock_service.files().get().execute.call_count == 1

    @patch('adapters.drive.get_drive_service')
    def test_drive_converts_403_to_permission_denied(self, mock_get_service) -> None:
        """Drive adapter converts 403 to PERMISSION_DENIED MiseError."""
        from adapters.drive import get_file_metadata

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        error_403 = Exception("Forbidden")
        error_403.resp = Mock(status=403)
        mock_service.files().get().execute.side_effect = error_403

        with pytest.raises(MiseError) as exc_info:
            get_file_metadata("restricted")

        assert exc_info.value.kind == ErrorKind.PERMISSION_DENIED

    @patch('adapters.gmail.get_gmail_service')
    def test_gmail_retries_on_rate_limit(self, mock_get_service) -> None:
        """Gmail adapter retries on 429 rate limit."""
        from adapters.gmail import search_threads

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        error_429 = Exception("Rate limited")
        error_429.resp = Mock(status=429)

        # First two calls: rate limited. Third: success.
        mock_service.users().threads().list().execute.side_effect = [
            error_429,
            error_429,
            {"threads": [], "resultSizeEstimate": 0},
        ]

        with patch('retry.time.sleep'):
            result = search_threads("test query", max_results=5)

        assert isinstance(result, list)
        assert mock_service.users().threads().list().execute.call_count == 3

    @patch('adapters.drive.get_drive_service')
    def test_exhausted_retries_raise_mise_error(self, mock_get_service) -> None:
        """After max attempts, raises MiseError with retryable flag."""
        from adapters.drive import get_file_metadata

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        error_503 = Exception("Service unavailable")
        error_503.resp = Mock(status=503)
        mock_service.files().get().execute.side_effect = error_503

        with patch('retry.time.sleep'):
            with pytest.raises(MiseError) as exc_info:
                get_file_metadata("abc")

        assert exc_info.value.kind == ErrorKind.NETWORK_ERROR
        assert exc_info.value.retryable
        # Default max_attempts=3
        assert mock_service.files().get().execute.call_count == 3
