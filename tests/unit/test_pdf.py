"""Unit tests for PDF fetching."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from tools.fetch import fetch_pdf


class TestFetchPdf:
    """Tests for PDF fetch functionality via Drive conversion."""

    @pytest.fixture
    def sample_pdf_bytes(self) -> bytes:
        """Sample PDF bytes for testing."""
        return b"%PDF-1.4 sample content"

    @patch("tools.fetch.get_drive_service")
    @patch("tools.fetch.download_file")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    def test_fetch_pdf_via_drive_conversion(
        self,
        mock_write_manifest: MagicMock,
        mock_write_content: MagicMock,
        mock_get_folder: MagicMock,
        mock_download: MagicMock,
        mock_get_service: MagicMock,
        sample_pdf_bytes: bytes,
        tmp_path: Path,
    ) -> None:
        """Test PDF fetch uses Drive conversion flow."""
        # Setup mocks
        mock_download.return_value = sample_pdf_bytes
        mock_get_folder.return_value = tmp_path / "pdf--test--abc123"
        mock_write_content.return_value = tmp_path / "content.md"

        # Mock Drive service
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        # Mock upload response
        mock_service.files().create().execute.return_value = {"id": "temp123"}

        # Mock export response
        mock_service.files().export().execute.return_value = b"# Converted Content\n\nThis is the PDF content."

        # Mock delete (no return needed)
        mock_service.files().delete().execute.return_value = None

        result = fetch_pdf(
            "abc123",
            "Test Document",
            {"mimeType": "application/pdf"},
        )

        # Verify result
        assert result.type == "pdf"
        assert result.format == "markdown"
        assert result.metadata["title"] == "Test Document"

        # Verify Drive conversion flow was used
        mock_download.assert_called_once_with("abc123")
        assert mock_service.files().create.called  # Upload with conversion
        assert mock_service.files().export.called  # Export as markdown
        assert mock_service.files().delete.called  # Cleanup temp file

    @patch("tools.fetch.get_drive_service")
    @patch("tools.fetch.download_file")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    def test_fetch_pdf_cleans_up_on_error(
        self,
        mock_write_manifest: MagicMock,
        mock_write_content: MagicMock,
        mock_get_folder: MagicMock,
        mock_download: MagicMock,
        mock_get_service: MagicMock,
        sample_pdf_bytes: bytes,
        tmp_path: Path,
    ) -> None:
        """Test that temp file is deleted even if export fails."""
        mock_download.return_value = sample_pdf_bytes
        mock_get_folder.return_value = tmp_path / "pdf--test--abc123"

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().create().execute.return_value = {"id": "temp123"}

        # Make export fail
        mock_service.files().export().execute.side_effect = Exception("Export failed")

        with pytest.raises(Exception, match="Export failed"):
            fetch_pdf("abc123", "Test", {})

        # Verify delete was still called for cleanup
        assert mock_service.files().delete.called
