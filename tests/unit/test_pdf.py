"""Unit tests for PDF extraction."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from adapters.pdf import (
    extract_pdf_content,
    fetch_and_extract_pdf,
    PdfExtractionResult,
    DEFAULT_MIN_CHARS_THRESHOLD,
    STREAMING_THRESHOLD_BYTES,
)
from adapters.drive import STREAMING_THRESHOLD_BYTES as DRIVE_STREAMING_THRESHOLD
from tools.fetch import fetch_pdf


class TestPdfExtraction:
    """Tests for PDF extraction adapter."""

    @pytest.fixture
    def sample_pdf_bytes(self) -> bytes:
        """Sample PDF bytes for testing."""
        return b"%PDF-1.4 sample content"

    @patch("adapters.pdf._extract_with_markitdown")
    def test_markitdown_success_above_threshold(
        self,
        mock_markitdown: MagicMock,
        sample_pdf_bytes: bytes,
    ) -> None:
        """Test that markitdown is used when it extracts enough content."""
        # Markitdown returns content above threshold
        mock_markitdown.return_value = "A" * (DEFAULT_MIN_CHARS_THRESHOLD + 100)

        result = extract_pdf_content(sample_pdf_bytes, "file123")

        assert result.method == "markitdown"
        assert result.char_count >= DEFAULT_MIN_CHARS_THRESHOLD
        assert len(result.warnings) == 0
        mock_markitdown.assert_called_once_with(sample_pdf_bytes)

    @patch("adapters.pdf.convert_via_drive")
    @patch("adapters.pdf._extract_with_markitdown")
    def test_drive_fallback_below_threshold(
        self,
        mock_markitdown: MagicMock,
        mock_convert: MagicMock,
        sample_pdf_bytes: bytes,
    ) -> None:
        """Test that Drive conversion is used when markitdown extracts too little."""
        # Markitdown returns content below threshold
        mock_markitdown.return_value = "A" * 100  # Below 500 default threshold

        # Drive conversion returns more content
        from adapters.conversion import ConversionResult
        mock_convert.return_value = ConversionResult(
            content="B" * 1000,
            temp_file_deleted=True,
            warnings=[],
        )

        result = extract_pdf_content(sample_pdf_bytes, "file123")

        assert result.method == "drive"
        assert result.char_count == 1000
        # Should have warning about fallback
        assert any("falling back to Drive" in w for w in result.warnings)
        mock_convert.assert_called_once()

    @patch("adapters.pdf._extract_with_markitdown")
    def test_custom_threshold(
        self,
        mock_markitdown: MagicMock,
        sample_pdf_bytes: bytes,
    ) -> None:
        """Test that custom threshold is respected."""
        # Return 200 chars - below default (500) but above custom (100)
        mock_markitdown.return_value = "A" * 200

        result = extract_pdf_content(
            sample_pdf_bytes,
            "file123",
            min_chars_threshold=100,
        )

        # Should use markitdown since 200 > 100
        assert result.method == "markitdown"
        assert result.char_count == 200

    @patch("adapters.pdf.convert_via_drive")
    @patch("adapters.pdf._extract_with_markitdown")
    def test_conversion_warnings_propagated(
        self,
        mock_markitdown: MagicMock,
        mock_convert: MagicMock,
        sample_pdf_bytes: bytes,
    ) -> None:
        """Test that Drive conversion warnings are included in result."""
        mock_markitdown.return_value = ""  # Force fallback

        from adapters.conversion import ConversionResult
        mock_convert.return_value = ConversionResult(
            content="content",
            temp_file_deleted=False,
            warnings=["Failed to delete temp file: _mise_temp_file123"],
        )

        result = extract_pdf_content(sample_pdf_bytes, "file123")

        assert "Failed to delete temp file" in str(result.warnings)


class TestFetchPdf:
    """Tests for PDF fetch tool function."""

    @patch("tools.fetch.fetch_and_extract_pdf")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    def test_fetch_pdf_deposits_to_workspace(
        self,
        mock_write_manifest: MagicMock,
        mock_write_content: MagicMock,
        mock_get_folder: MagicMock,
        mock_extract: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test that fetch_pdf deposits content to workspace."""
        mock_extract.return_value = PdfExtractionResult(
            content="# PDF Content",
            method="markitdown",
            char_count=100,
            warnings=[],
        )
        mock_get_folder.return_value = tmp_path / "pdf--test--abc123"
        mock_write_content.return_value = tmp_path / "content.md"

        result = fetch_pdf("abc123", "Test Document", {"mimeType": "application/pdf"})

        assert result.type == "pdf"
        assert result.format == "markdown"
        assert result.metadata["title"] == "Test Document"
        assert result.metadata["extraction_method"] == "markitdown"

        mock_extract.assert_called_once_with("abc123")
        mock_write_content.assert_called_once()
        mock_write_manifest.assert_called_once()

    @patch("tools.fetch.fetch_and_extract_pdf")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    def test_fetch_pdf_includes_warnings_in_manifest(
        self,
        mock_write_manifest: MagicMock,
        mock_write_content: MagicMock,
        mock_get_folder: MagicMock,
        mock_extract: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test that warnings are included in manifest."""
        mock_extract.return_value = PdfExtractionResult(
            content="content",
            method="drive",
            char_count=500,
            warnings=["Fallback warning"],
        )
        mock_get_folder.return_value = tmp_path / "pdf--test--abc123"
        mock_write_content.return_value = tmp_path / "content.md"

        fetch_pdf("abc123", "Test", {})

        # Check that warnings were passed to manifest
        call_args = mock_write_manifest.call_args
        extra = call_args.kwargs.get("extra") or call_args[1].get("extra") or (call_args[0][4] if len(call_args[0]) > 4 else {})
        assert "warnings" in extra
        assert "Fallback warning" in extra["warnings"]


class TestLargeFileStreaming:
    """Tests for large file streaming download support."""

    def test_streaming_threshold_is_50mb(self) -> None:
        """Verify streaming threshold is 50MB."""
        assert DRIVE_STREAMING_THRESHOLD == 50 * 1024 * 1024
        assert STREAMING_THRESHOLD_BYTES == 50 * 1024 * 1024

    @patch("adapters.pdf.get_file_size")
    @patch("adapters.pdf.download_file")
    @patch("adapters.pdf._extract_with_markitdown")
    def test_small_file_uses_memory_download(
        self,
        mock_markitdown: MagicMock,
        mock_download: MagicMock,
        mock_get_size: MagicMock,
    ) -> None:
        """Test that files under threshold use memory download."""
        # Small file (10MB)
        mock_get_size.return_value = 10 * 1024 * 1024
        mock_download.return_value = b"PDF content"
        mock_markitdown.return_value = "Extracted content " * 100

        result = fetch_and_extract_pdf("small_file_id")

        # Should use memory download
        mock_download.assert_called_once_with("small_file_id")
        assert result.method == "markitdown"

    @patch("adapters.pdf.get_file_size")
    @patch("adapters.pdf.download_file_to_temp")
    @patch("adapters.pdf.MarkItDown")
    def test_large_file_uses_streaming_download(
        self,
        mock_markitdown_class: MagicMock,
        mock_download_temp: MagicMock,
        mock_get_size: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test that files over threshold use streaming download to temp."""
        # Large file (100MB)
        mock_get_size.return_value = 100 * 1024 * 1024

        # Create a temp file for the mock
        temp_file = tmp_path / "large.pdf"
        temp_file.write_bytes(b"PDF content")
        mock_download_temp.return_value = temp_file

        # Mock markitdown
        mock_md_instance = MagicMock()
        mock_md_instance.convert_local.return_value = MagicMock(
            text_content="Extracted from large file " * 100
        )
        mock_markitdown_class.return_value = mock_md_instance

        result = fetch_and_extract_pdf("large_file_id")

        # Should use streaming download
        mock_download_temp.assert_called_once_with("large_file_id", suffix=".pdf")
        assert result.method == "markitdown"
        # Should have warning about large file
        assert any("Large file" in w for w in result.warnings)

    @patch("adapters.pdf.get_file_size")
    @patch("adapters.pdf.download_file_to_temp")
    @patch("adapters.pdf.convert_via_drive")
    @patch("adapters.pdf.MarkItDown")
    def test_large_file_drive_fallback_reads_from_temp(
        self,
        mock_markitdown_class: MagicMock,
        mock_convert: MagicMock,
        mock_download_temp: MagicMock,
        mock_get_size: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test that large file Drive fallback reads from temp file."""
        # Large file
        mock_get_size.return_value = 100 * 1024 * 1024

        # Create temp file with content
        temp_file = tmp_path / "large.pdf"
        temp_content = b"PDF binary content for conversion"
        temp_file.write_bytes(temp_content)
        mock_download_temp.return_value = temp_file

        # Markitdown fails (low char count)
        mock_md_instance = MagicMock()
        mock_md_instance.convert_local.return_value = MagicMock(text_content="short")
        mock_markitdown_class.return_value = mock_md_instance

        # Drive conversion succeeds
        from adapters.conversion import ConversionResult
        mock_convert.return_value = ConversionResult(
            content="Extracted via Drive " * 100,
            temp_file_deleted=True,
            warnings=[],
        )

        result = fetch_and_extract_pdf("large_file_id")

        # Should fall back to Drive
        assert result.method == "drive"
        # Convert should be called with bytes read from temp file
        mock_convert.assert_called_once()
        call_kwargs = mock_convert.call_args.kwargs
        assert call_kwargs["file_bytes"] == temp_content
