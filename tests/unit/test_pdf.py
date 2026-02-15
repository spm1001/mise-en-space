"""Unit tests for PDF extraction."""

import re

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from adapters.pdf import (
    extract_pdf_content,
    fetch_and_extract_pdf,
    PdfExtractionResult,
    DEFAULT_MIN_CHARS_THRESHOLD,
    STREAMING_THRESHOLD_BYTES,
    _looks_like_flattened_tables,
)
from adapters.drive import STREAMING_THRESHOLD_BYTES as DRIVE_STREAMING_THRESHOLD
from tools.fetch import fetch_pdf

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"


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

    @patch("adapters.pdf._extract_with_markitdown")
    def test_threshold_boundary_exactly_at(
        self,
        mock_markitdown: MagicMock,
        sample_pdf_bytes: bytes,
    ) -> None:
        """Test that exactly threshold chars uses markitdown (>= comparison)."""
        mock_markitdown.return_value = "A" * DEFAULT_MIN_CHARS_THRESHOLD  # Exactly 500

        result = extract_pdf_content(sample_pdf_bytes, "file123")

        # Should use markitdown (>= threshold)
        assert result.method == "markitdown"
        assert result.char_count == DEFAULT_MIN_CHARS_THRESHOLD

    @patch("adapters.pdf.convert_via_drive")
    @patch("adapters.pdf._extract_with_markitdown")
    def test_threshold_boundary_one_below(
        self,
        mock_markitdown: MagicMock,
        mock_convert: MagicMock,
        sample_pdf_bytes: bytes,
    ) -> None:
        """Test that one char below threshold triggers fallback."""
        mock_markitdown.return_value = "A" * (DEFAULT_MIN_CHARS_THRESHOLD - 1)  # 499

        from adapters.conversion import ConversionResult
        mock_convert.return_value = ConversionResult(
            content="B" * 1000,
            temp_file_deleted=True,
            warnings=[],
        )

        result = extract_pdf_content(sample_pdf_bytes, "file123")

        # Should fall back to Drive since 499 < 500
        assert result.method == "drive"

    @patch("adapters.pdf.convert_via_drive")
    @patch("adapters.pdf._extract_with_markitdown")
    def test_empty_content_triggers_fallback(
        self,
        mock_markitdown: MagicMock,
        mock_convert: MagicMock,
        sample_pdf_bytes: bytes,
    ) -> None:
        """Test that empty markitdown result triggers fallback."""
        mock_markitdown.return_value = ""  # Empty

        from adapters.conversion import ConversionResult
        mock_convert.return_value = ConversionResult(
            content="B" * 500,
            temp_file_deleted=True,
            warnings=[],
        )

        result = extract_pdf_content(sample_pdf_bytes, "file123")

        # Should fall back to Drive
        assert result.method == "drive"
        assert any("0 chars" in w for w in result.warnings)

    @patch("adapters.pdf.convert_via_drive")
    @patch("adapters.pdf._extract_with_markitdown")
    def test_whitespace_only_counts_as_zero(
        self,
        mock_markitdown: MagicMock,
        mock_convert: MagicMock,
        sample_pdf_bytes: bytes,
    ) -> None:
        """Test that whitespace-only content is counted as zero chars after strip."""
        mock_markitdown.return_value = "   \n\t\n   "  # Only whitespace

        from adapters.conversion import ConversionResult
        mock_convert.return_value = ConversionResult(
            content="Real content",
            temp_file_deleted=True,
            warnings=[],
        )

        result = extract_pdf_content(sample_pdf_bytes, "file123")

        # Should fall back to Drive because stripped content is 0 chars
        assert result.method == "drive"
        assert any("0 chars" in w for w in result.warnings)

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

    @patch("tools.fetch.drive.fetch_and_extract_pdf")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive.write_manifest")
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

    @patch("tools.fetch.drive.fetch_and_extract_pdf")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive.write_manifest")
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
        # Convert should be called with file_path (streaming, not bytes in memory)
        mock_convert.assert_called_once()
        call_kwargs = mock_convert.call_args.kwargs
        assert call_kwargs["file_path"] == temp_file


class TestConvertViaDriveValidation:
    """Tests for convert_via_drive input validation."""

    def test_requires_either_bytes_or_path(self):
        """Must provide at least one of file_bytes, file_path, or source_file_id."""
        from adapters.conversion import convert_via_drive
        from models import MiseError

        # Retry decorator converts ValueError to MiseError
        with pytest.raises(MiseError, match="Must provide file_bytes, file_path, or source_file_id"):
            convert_via_drive(source_mime="application/pdf", target_type="doc")

    def test_rejects_both_bytes_and_path(self, tmp_path: Path):
        """Cannot provide both file_bytes and file_path."""
        from adapters.conversion import convert_via_drive
        from models import MiseError

        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"test")

        # Retry decorator converts ValueError to MiseError
        with pytest.raises(MiseError, match="Cannot provide both file_bytes and file_path"):
            convert_via_drive(
                file_bytes=b"test",
                file_path=test_file,
                source_mime="application/pdf",
                target_type="doc",
            )

    @patch("adapters.conversion.get_drive_service")
    def test_source_file_id_uses_copy_not_upload(self, mock_service_fn):
        """source_file_id uses files().copy() instead of files().create()."""
        from adapters.conversion import convert_via_drive

        mock_service = MagicMock()
        mock_service_fn.return_value = mock_service
        mock_service.files().copy().execute.return_value = {"id": "temp_copy_id"}
        mock_service.files().export().execute.return_value = b"converted content"
        mock_service.files().delete().execute.return_value = None

        result = convert_via_drive(
            source_file_id="existing_drive_file",
            target_type="doc",
            export_format="markdown",
        )

        # copy() called, create() not called
        mock_service.files().copy.assert_called()
        copy_kwargs = mock_service.files().copy.call_args
        assert copy_kwargs.kwargs["fileId"] == "existing_drive_file"
        assert result.content == "converted content"


class TestFlattenedTableDetection:
    """Tests for _looks_like_flattened_tables() heuristic.

    Uses a real fixture (sanitized media rate-card) plus synthetic content
    to verify detection triggers on flattened data and doesn't false-positive
    on prose, code, poetry, numbered lists, or short content.
    """

    @pytest.fixture
    def rate_card_content(self) -> str:
        """Load the flattened rate-card fixture."""
        path = FIXTURES_DIR / "pdf" / "flattened_table_rate_card.txt"
        return path.read_text()

    # --- Real fixture: should trigger ---

    def test_fixture_detected_as_flattened(self, rate_card_content: str) -> None:
        """Real rate-card fixture triggers the heuristic."""
        assert _looks_like_flattened_tables(rate_card_content)

    def test_fixture_passes_char_count_gate(self, rate_card_content: str) -> None:
        """Fixture passes the char-count gate — that's the whole problem."""
        assert len(rate_card_content.strip()) >= DEFAULT_MIN_CHARS_THRESHOLD

    # --- Synthetic: should trigger ---

    def test_synthetic_flattened_data(self) -> None:
        """Synthetic flattened table data triggers detection."""
        lines = []
        for i in range(50):
            lines.extend([
                "Channel A",
                f"{i * 100 + 50}",
                f"£{i * 10:.2f}",
                "Peak",
            ])
        content = "\n".join(lines)
        assert _looks_like_flattened_tables(content)

    # --- Should NOT trigger ---

    def test_normal_prose(self) -> None:
        """Normal text paragraphs don't trigger."""
        lines = [
            "The quarterly revenue report shows strong growth across all regions.",
            "European markets performed particularly well in the second quarter.",
            "The Asia-Pacific region saw a 15% increase in customer acquisition rates.",
            "Management expects continued momentum through the remainder of the fiscal year.",
            "Key performance indicators remain above target for most business units.",
        ] * 10
        assert not _looks_like_flattened_tables("\n".join(lines))

    def test_code_content(self) -> None:
        """Source code doesn't trigger (mixed line lengths, low numeric)."""
        lines = [
            "def process_data(input_file: str) -> dict:",
            '    """Process the input data file."""',
            '    with open(input_file, "r") as f:',
            "        data = json.load(f)",
            "    results = {}",
            "    for key, value in data.items():",
            "        results[key] = transform(value)",
            "    return results",
            "",
            "class DataProcessor:",
            '    """Handles data transformation pipeline."""',
            "",
            "    def __init__(self, config: Config):",
            "        self.config = config",
            "        self.logger = logging.getLogger(__name__)",
        ] * 5
        assert not _looks_like_flattened_tables("\n".join(lines))

    def test_poetry(self) -> None:
        """Poetry has high short_ratio but very low numeric_ratio."""
        lines = [
            "Shall I compare",
            "thee to a",
            "summer's day?",
            "Thou art more",
            "lovely and",
            "more temperate",
            "Rough winds do",
            "shake the",
            "darling buds",
            "of May",
        ] * 5
        assert not _looks_like_flattened_tables("\n".join(lines))

    def test_numbered_list(self) -> None:
        """Numbered lists have digits but high sentence_ratio."""
        lines = [
            f"{i}. This is a detailed action item that describes what needs to happen next in the project."
            for i in range(1, 30)
        ]
        assert not _looks_like_flattened_tables("\n".join(lines))

    def test_short_content_skipped(self) -> None:
        """Content with fewer than 20 non-empty lines is skipped."""
        lines = ["100", "200", "Channel A"] * 5  # 15 lines
        assert not _looks_like_flattened_tables("\n".join(lines))

    def test_markdown_table_syntax_skipped(self) -> None:
        """Content with markdown table pipes is skipped (structure preserved)."""
        lines = [
            "| Channel | CPM | Rate |",
            "|---------|-----|------|",
        ]
        lines.extend([f"| Ch{i} | {i*100} | £{i*10} |" for i in range(30)])
        assert not _looks_like_flattened_tables("\n".join(lines))

    # --- Integration: extract_pdf_content triggers Drive fallback ---

    @patch("adapters.pdf.convert_via_drive")
    @patch("adapters.pdf._extract_with_markitdown")
    def test_flattened_content_triggers_drive_fallback(
        self,
        mock_markitdown: MagicMock,
        mock_convert: MagicMock,
    ) -> None:
        """Markitdown content above char threshold but flattened triggers Drive."""
        # Build flattened content above char threshold
        lines = []
        for i in range(80):
            lines.extend(["Channel", f"{i * 100}", f"£{i:.2f}"])
        mock_markitdown.return_value = "\n".join(lines)

        from adapters.conversion import ConversionResult
        mock_convert.return_value = ConversionResult(
            content="| Channel | CPM |\n|---------|-----|\n| A | 100 |",
            temp_file_deleted=True,
            warnings=[],
        )

        result = extract_pdf_content(b"%PDF-test", "file123")

        assert result.method == "drive"
        assert any("flattened tables" in w for w in result.warnings)
        mock_convert.assert_called_once()
