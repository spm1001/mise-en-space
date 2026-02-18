"""Unit tests for Office file extraction."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from adapters.office import (
    extract_office_content,
    get_office_type_from_mime,
    OfficeExtractionResult,
    OFFICE_FORMATS,
)
from tools.fetch import fetch_office


class TestOfficeExtraction:
    """Tests for Office extraction adapter."""

    @patch("adapters.office.convert_via_drive")
    def test_extract_docx(self, mock_convert: MagicMock) -> None:
        """Test DOCX extraction uses markdown export."""
        from adapters.conversion import ConversionResult
        mock_convert.return_value = ConversionResult(
            content="# Document Title\n\nSome content.",
            temp_file_deleted=True,
            warnings=[],
        )

        result = extract_office_content("docx", file_bytes=b"fake docx bytes", file_id="file123")

        assert result.source_type == "docx"
        assert result.export_format == "markdown"
        assert result.extension == "md"
        assert "Document Title" in result.content

        # Verify correct conversion params
        mock_convert.assert_called_once()
        call_kwargs = mock_convert.call_args.kwargs
        assert call_kwargs["target_type"] == "doc"
        assert call_kwargs["export_format"] == "markdown"

    @patch("adapters.office.delete_temp_file", return_value=True)
    @patch("adapters.office.extract_sheets_content")
    @patch("adapters.office.fetch_spreadsheet")
    @patch("adapters.office.upload_and_convert", return_value="temp_sheet_id")
    def test_extract_xlsx(
        self,
        mock_upload: MagicMock,
        mock_fetch_sheet: MagicMock,
        mock_extract_sheets: MagicMock,
        mock_delete: MagicMock,
    ) -> None:
        """Test XLSX extraction uses Sheets API for all tabs."""
        from models import SpreadsheetData, SheetTab
        mock_fetch_sheet.return_value = SpreadsheetData(
            title="Test",
            spreadsheet_id="temp_sheet_id",
            sheets=[
                SheetTab(name="Sheet1", values=[["Name", "Value"], ["Alice", "100"]]),
                SheetTab(name="Sheet2", values=[["ID", "Desc"], ["1", "Widget"]]),
            ],
        )
        mock_extract_sheets.return_value = (
            "=== Sheet: Sheet1 ===\nName,Value\nAlice,100\n\n"
            "=== Sheet: Sheet2 ===\nID,Desc\n1,Widget"
        )

        result = extract_office_content("xlsx", file_bytes=b"fake xlsx bytes", file_id="file123")

        assert result.source_type == "xlsx"
        assert result.export_format == "csv"
        assert result.extension == "csv"
        assert "Alice" in result.content
        assert "Sheet2" in result.content  # Both tabs present
        assert "Widget" in result.content

        # Verify Sheets API path used (not CSV export)
        mock_upload.assert_called_once()
        mock_fetch_sheet.assert_called_once_with("temp_sheet_id", render_charts=False)
        mock_delete.assert_called_once()

    @patch("adapters.office.convert_via_drive")
    def test_extract_pptx(self, mock_convert: MagicMock) -> None:
        """Test PPTX extraction uses plain text export."""
        from adapters.conversion import ConversionResult
        mock_convert.return_value = ConversionResult(
            content="Slide 1: Title\nSlide 2: Content",
            temp_file_deleted=True,
            warnings=[],
        )

        result = extract_office_content("pptx", file_bytes=b"fake pptx bytes", file_id="file123")

        assert result.source_type == "pptx"
        assert result.export_format == "plain"
        assert result.extension == "txt"

        call_kwargs = mock_convert.call_args.kwargs
        assert call_kwargs["target_type"] == "slides"
        assert call_kwargs["export_format"] == "plain"

    @patch("adapters.office.convert_via_drive")
    def test_warnings_propagated(self, mock_convert: MagicMock) -> None:
        """Test that conversion warnings are included in result."""
        from adapters.conversion import ConversionResult
        mock_convert.return_value = ConversionResult(
            content="content",
            temp_file_deleted=False,
            warnings=["Failed to delete temp file"],
        )

        result = extract_office_content("docx", file_bytes=b"bytes", file_id="file123")

        assert "Failed to delete temp file" in result.warnings


class TestOfficeTypeDetection:
    """Tests for MIME type to Office type mapping."""

    def test_docx_mime(self) -> None:
        """Test DOCX MIME type detection."""
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert get_office_type_from_mime(mime) == "docx"

    def test_xlsx_mime(self) -> None:
        """Test XLSX MIME type detection."""
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert get_office_type_from_mime(mime) == "xlsx"

    def test_pptx_mime(self) -> None:
        """Test PPTX MIME type detection."""
        mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        assert get_office_type_from_mime(mime) == "pptx"

    def test_unknown_mime(self) -> None:
        """Test unknown MIME type returns None."""
        assert get_office_type_from_mime("application/pdf") is None
        assert get_office_type_from_mime("text/plain") is None


class TestFetchOffice:
    """Tests for Office fetch tool function."""

    @patch("tools.fetch.drive.fetch_and_extract_office")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive.write_manifest")
    def test_fetch_office_deposits_to_workspace(
        self,
        mock_write_manifest: MagicMock,
        mock_write_content: MagicMock,
        mock_get_folder: MagicMock,
        mock_extract: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test that fetch_office deposits content to workspace."""
        mock_extract.return_value = OfficeExtractionResult(
            content="# Document",
            source_type="docx",
            export_format="markdown",
            extension="md",
            warnings=[],
        )
        mock_get_folder.return_value = tmp_path / "docx--test--abc123"
        mock_write_content.return_value = tmp_path / "content.md"

        result = fetch_office("abc123", "Test.docx", {}, "docx")

        assert result.type == "docx"
        assert result.format == "markdown"
        assert result.metadata["title"] == "Test.docx"

        mock_extract.assert_called_once_with("abc123", "docx")
        mock_write_content.assert_called_once()

    @patch("tools.fetch.drive.fetch_and_extract_office")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive.write_manifest")
    def test_fetch_xlsx_uses_csv_format(
        self,
        mock_write_manifest: MagicMock,
        mock_write_content: MagicMock,
        mock_get_folder: MagicMock,
        mock_extract: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test that XLSX fetch uses CSV format."""
        mock_extract.return_value = OfficeExtractionResult(
            content="A,B,C",
            source_type="xlsx",
            export_format="csv",
            extension="csv",
            warnings=[],
        )
        mock_get_folder.return_value = tmp_path / "xlsx--test--abc123"
        mock_write_content.return_value = tmp_path / "content.csv"

        result = fetch_office("abc123", "Data.xlsx", {}, "xlsx")

        assert result.type == "xlsx"
        assert result.format == "csv"

        # Verify filename is content.csv
        call_args = mock_write_content.call_args
        assert call_args.kwargs.get("filename") == "content.csv" or "content.csv" in str(call_args)

    @patch("tools.fetch.drive.fetch_and_extract_office")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive.write_manifest")
    def test_fetch_xlsx_deposits_raw_file(
        self,
        mock_write_manifest: MagicMock,
        mock_write_content: MagicMock,
        mock_get_folder: MagicMock,
        mock_extract: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test that XLSX fetch deposits raw .xlsx alongside content.csv."""
        fake_xlsx_bytes = b"PK\x03\x04fake-xlsx-content"
        deposit_folder = tmp_path / "xlsx--budget--abc123"
        deposit_folder.mkdir()

        mock_extract.return_value = OfficeExtractionResult(
            content="A,B\n1,2",
            source_type="xlsx",
            export_format="csv",
            extension="csv",
            warnings=[],
            raw_bytes=fake_xlsx_bytes,
        )
        mock_get_folder.return_value = deposit_folder
        mock_write_content.return_value = deposit_folder / "content.csv"

        result = fetch_office("abc123", "Budget.xlsx", {}, "xlsx")

        # Raw xlsx deposited with original filename
        raw_path = deposit_folder / "Budget.xlsx"
        assert raw_path.exists()
        assert raw_path.read_bytes() == fake_xlsx_bytes

        # Manifest includes raw_file with original name
        manifest_call = mock_write_manifest.call_args
        extra = manifest_call.kwargs.get("extra") or manifest_call[1].get("extra")
        assert extra["raw_file"] == "Budget.xlsx"

    @patch("tools.fetch.drive.fetch_and_extract_office")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive.write_manifest")
    def test_fetch_xlsx_large_file_copies_instead_of_reading(
        self,
        mock_write_manifest: MagicMock,
        mock_write_content: MagicMock,
        mock_get_folder: MagicMock,
        mock_extract: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Large XLSX uses file copy (raw_temp_path) instead of read_bytes."""
        fake_xlsx_bytes = b"PK\x03\x04large-xlsx-content"
        deposit_folder = tmp_path / "xlsx--big-budget--abc123"
        deposit_folder.mkdir()

        # Simulate the streaming path: raw_temp_path set, raw_bytes is None
        temp_file = tmp_path / "streamed.xlsx"
        temp_file.write_bytes(fake_xlsx_bytes)

        mock_extract.return_value = OfficeExtractionResult(
            content="col1\n1",
            source_type="xlsx",
            export_format="csv",
            extension="csv",
            warnings=["Large file: used streaming download"],
            raw_temp_path=temp_file,
        )
        mock_get_folder.return_value = deposit_folder
        mock_write_content.return_value = deposit_folder / "content.csv"

        fetch_office("abc123", "Big Budget.xlsx", {}, "xlsx")

        # Raw xlsx deposited via copy
        raw_path = deposit_folder / "Big Budget.xlsx"
        assert raw_path.exists()
        assert raw_path.read_bytes() == fake_xlsx_bytes

        # Temp file cleaned up after copy
        assert not temp_file.exists()

        # Manifest includes raw_file
        manifest_call = mock_write_manifest.call_args
        extra = manifest_call.kwargs.get("extra") or manifest_call[1].get("extra")
        assert extra["raw_file"] == "Big Budget.xlsx"

    @patch("tools.fetch.drive.fetch_and_extract_office")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive.write_manifest")
    def test_fetch_docx_no_raw_file(
        self,
        mock_write_manifest: MagicMock,
        mock_write_content: MagicMock,
        mock_get_folder: MagicMock,
        mock_extract: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test that non-XLSX Office files don't get raw file deposits."""
        deposit_folder = tmp_path / "docx--report--def456"
        deposit_folder.mkdir()

        mock_extract.return_value = OfficeExtractionResult(
            content="# Report",
            source_type="docx",
            export_format="markdown",
            extension="md",
            warnings=[],
        )
        mock_get_folder.return_value = deposit_folder
        mock_write_content.return_value = deposit_folder / "content.md"

        fetch_office("def456", "Report.docx", {}, "docx")

        # No raw file for docx
        assert not (deposit_folder / "source.xlsx").exists()
        assert not (deposit_folder / "source.docx").exists()
