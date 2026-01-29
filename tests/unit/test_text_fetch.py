"""Tests for text file fetch functionality."""

import pytest
from unittest.mock import patch, MagicMock
from tools.fetch import fetch_text, is_text_file, TEXT_MIME_TYPES


class TestIsTextFile:
    """Tests for is_text_file() helper."""

    def test_explicit_text_types(self):
        """Recognizes explicitly listed text MIME types."""
        for mime_type in TEXT_MIME_TYPES:
            assert is_text_file(mime_type), f"Should recognize {mime_type}"

    def test_generic_text_types(self):
        """Recognizes any text/* MIME type."""
        assert is_text_file("text/plain")
        assert is_text_file("text/x-python")
        assert is_text_file("text/x-custom")

    def test_non_text_types(self):
        """Rejects non-text MIME types."""
        assert not is_text_file("application/pdf")
        assert not is_text_file("image/png")
        assert not is_text_file("video/mp4")
        assert not is_text_file("application/vnd.google-apps.document")


class TestFetchText:
    """Tests for fetch_text() function."""

    @patch("tools.fetch.download_file")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    def test_fetch_plain_text(self, mock_manifest, mock_write, mock_folder, mock_download):
        """Fetches plain text file and deposits correctly."""
        mock_download.return_value = b"Hello, world!"
        mock_folder.return_value = "/tmp/mise-fetch/text--test--abc123"
        mock_write.return_value = "/tmp/mise-fetch/text--test--abc123/content.txt"

        metadata = {"mimeType": "text/plain", "name": "test.txt"}
        result = fetch_text("abc123", "test.txt", metadata)

        assert result.type == "text"
        assert result.format == "text"
        assert result.metadata["char_count"] == 13
        mock_write.assert_called_once()
        # Check filename is content.txt for text/plain
        call_args = mock_write.call_args
        assert call_args[1]["filename"] == "content.txt"

    @patch("tools.fetch.download_file")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    def test_fetch_json(self, mock_manifest, mock_write, mock_folder, mock_download):
        """Fetches JSON file with correct format and extension."""
        mock_download.return_value = b'{"key": "value"}'
        mock_folder.return_value = "/tmp/mise-fetch/text--data--xyz789"
        mock_write.return_value = "/tmp/mise-fetch/text--data--xyz789/content.json"

        metadata = {"mimeType": "application/json", "name": "data.json"}
        result = fetch_text("xyz789", "data.json", metadata)

        assert result.type == "text"
        assert result.format == "json"
        assert result.metadata["mime_type"] == "application/json"
        call_args = mock_write.call_args
        assert call_args[1]["filename"] == "content.json"

    @patch("tools.fetch.download_file")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    def test_fetch_csv(self, mock_manifest, mock_write, mock_folder, mock_download):
        """Fetches CSV file with correct format and extension."""
        mock_download.return_value = b"a,b,c\n1,2,3"
        mock_folder.return_value = "/tmp/mise-fetch/text--data--csv123"
        mock_write.return_value = "/tmp/mise-fetch/text--data--csv123/content.csv"

        metadata = {"mimeType": "text/csv", "name": "data.csv"}
        result = fetch_text("csv123", "data.csv", metadata)

        assert result.type == "text"
        assert result.format == "csv"
        call_args = mock_write.call_args
        assert call_args[1]["filename"] == "content.csv"

    @patch("tools.fetch.download_file")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    def test_fetch_with_email_context(self, mock_manifest, mock_write, mock_folder, mock_download):
        """Includes email_context when provided."""
        from models import EmailContext

        mock_download.return_value = b"content"
        mock_folder.return_value = "/tmp/test"
        mock_write.return_value = "/tmp/test/content.txt"

        email_ctx = EmailContext(
            message_id="msg123",
            from_address="test@example.com",
            subject="Test email",
        )
        metadata = {"mimeType": "text/plain", "name": "attachment.txt"}
        result = fetch_text("file123", "attachment.txt", metadata, email_context=email_ctx)

        assert "email_context" in result.metadata
        assert result.metadata["email_context"]["message_id"] == "msg123"

    @patch("tools.fetch.download_file")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    def test_handles_unicode(self, mock_manifest, mock_write, mock_folder, mock_download):
        """Handles Unicode content correctly."""
        mock_download.return_value = "Hello, 世界! 🎉".encode("utf-8")
        mock_folder.return_value = "/tmp/test"
        mock_write.return_value = "/tmp/test/content.txt"

        metadata = {"mimeType": "text/plain", "name": "unicode.txt"}
        result = fetch_text("uni123", "unicode.txt", metadata)

        # Verify content was decoded
        written_content = mock_write.call_args[0][1]
        assert "世界" in written_content
        assert "🎉" in written_content
