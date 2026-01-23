"""Unit tests for sheets extractor."""

import pytest

from extractors.sheets import extract_sheets_content, _row_to_csv
from models import SpreadsheetData, SheetTab


# Fixture 'sheets_response' is provided by tests/conftest.py


class TestExtractSheetsContent:
    """Tests for the main extraction function."""

    def test_basic_extraction(self, sheets_response: SpreadsheetData) -> None:
        """Test that sheets are extracted with proper headers."""
        result = extract_sheets_content(sheets_response)

        # Check sheet headers present
        assert "=== Sheet: Summary ===" in result
        assert "=== Sheet: Details ===" in result
        assert "=== Sheet: Empty Sheet ===" in result

        # Check empty sheet handling
        assert "(empty)" in result

    def test_csv_content(self, sheets_response: SpreadsheetData) -> None:
        """Test that content is properly formatted as CSV."""
        result = extract_sheets_content(sheets_response)

        # Basic CSV row
        assert "Category,Amount,Notes" in result
        assert "Revenue,1000000,Projected" in result

    def test_csv_escaping_quotes(self, sheets_response: SpreadsheetData) -> None:
        """Test that quotes in cells are properly escaped."""
        result = extract_sheets_content(sheets_response)

        # Cell with quotes: 'Includes "contingency"' becomes '"Includes ""contingency"""'
        # The inner quotes are doubled, whole cell is wrapped in quotes
        assert '"Includes ""contingency"""' in result

    def test_csv_escaping_commas(self, sheets_response: SpreadsheetData) -> None:
        """Test that commas in cells are properly quoted."""
        result = extract_sheets_content(sheets_response)

        # Cell with comma: 'Service B, Premium' should be quoted
        assert '"Service B, Premium"' in result

    def test_csv_escaping_newlines(self, sheets_response: SpreadsheetData) -> None:
        """Test that newlines in cells are properly quoted."""
        result = extract_sheets_content(sheets_response)

        # Cell with newlines should be quoted
        assert '"Multi\nline\nnote"' in result

    def test_truncation(self) -> None:
        """Test that content is truncated at max_length."""
        data = SpreadsheetData(
            title="Big Spreadsheet",
            spreadsheet_id="big-id",
            sheets=[
                SheetTab(name="Big Sheet", values=[["x"] * 100 for _ in range(100)])
            ],
        )

        result = extract_sheets_content(data, max_length=500)

        assert len(result) <= 600  # Some buffer for truncation message
        assert "TRUNCATED" in result

    def test_empty_spreadsheet(self) -> None:
        """Test handling of spreadsheet with no sheets."""
        data = SpreadsheetData(
            title="Empty",
            spreadsheet_id="empty-id",
            sheets=[],
        )

        result = extract_sheets_content(data)

        assert result == ""


class TestRowToCsv:
    """Tests for the CSV row helper."""

    def test_simple_row(self) -> None:
        """Test simple row without special characters."""
        result = _row_to_csv(["a", "b", "c"])
        assert result == "a,b,c"

    def test_none_values(self) -> None:
        """Test that None values become empty strings."""
        result = _row_to_csv(["a", None, "c"])
        assert result == "a,,c"

    def test_numeric_values(self) -> None:
        """Test that numbers are converted to strings."""
        result = _row_to_csv([1, 2.5, "three"])
        assert result == "1,2.5,three"

    def test_quote_escaping(self) -> None:
        """Test that quotes are doubled and cell is quoted."""
        result = _row_to_csv(['Say "hello"'])
        assert result == '"Say ""hello"""'

    def test_comma_escaping(self) -> None:
        """Test that cells with commas are quoted."""
        result = _row_to_csv(["a,b"])
        assert result == '"a,b"'

    def test_newline_escaping(self) -> None:
        """Test that cells with newlines are quoted."""
        result = _row_to_csv(["line1\nline2"])
        assert result == '"line1\nline2"'
