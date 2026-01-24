"""Unit tests for PDF extraction."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from adapters.pdf import (
    extract_pdf_content,
    PdfExtractionResult,
    DEFAULT_MIN_CHARS_THRESHOLD,
)
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
