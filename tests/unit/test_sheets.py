"""Unit tests for sheets extractor."""

import pytest
from inline_snapshot import snapshot

from extractors.sheets import extract_sheets_content, _row_to_csv
from models import SpreadsheetData, SheetTab


# Fixture 'sheets_response' is provided by tests/conftest.py


class TestExtractSheetsContent:
    """Tests for the main extraction function."""

    def test_basic_extraction(self, sheets_response: SpreadsheetData) -> None:
        """Test full extraction output with snapshot."""
        result = extract_sheets_content(sheets_response)
        assert result == snapshot('''\
=== Sheet: Summary ===
Category,Amount,Notes
Revenue,1000000,Projected
Expenses,750000,"Includes ""contingency"""
Net,250000,Before taxes

=== Sheet: Details ===
ID,Description,Amount
1,Widget A,500
2,"Service B, Premium",1500
3,"Multi
line
note",200

=== Sheet: Empty Sheet ===
(empty)\
''')

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

    @pytest.mark.parametrize("cell,expected", [
        ('Say "hello"', '"Say ""hello"""'),  # Quotes doubled and wrapped
        ("a,b", '"a,b"'),                    # Commas wrapped
        ("line1\nline2", '"line1\nline2"'),  # Newlines wrapped
    ])
    def test_csv_special_char_escaping(self, cell, expected) -> None:
        """Test that special characters are properly escaped."""
        result = _row_to_csv([cell])
        assert result == expected
