"""
Tests for sheets adapter using mocked services.

Mocks the Sheets API service, feeds it API-shaped responses,
and verifies the adapter parses into SpreadsheetData correctly.
"""

import pytest
from unittest.mock import patch, MagicMock

from models import SpreadsheetData
from adapters.sheets import fetch_spreadsheet, _parse_cell_value, _parse_row


# ============================================================================
# PURE HELPERS
# ============================================================================

class TestParseCellValue:
    """Test cell value type conversion."""

    def test_string(self) -> None:
        assert _parse_cell_value("hello") == "hello"

    def test_integer(self) -> None:
        assert _parse_cell_value(42) == 42

    def test_float(self) -> None:
        assert _parse_cell_value(3.14) == 3.14

    def test_bool(self) -> None:
        assert _parse_cell_value(True) is True

    def test_none(self) -> None:
        assert _parse_cell_value(None) is None

    def test_other_type_becomes_string(self) -> None:
        assert _parse_cell_value(["list"]) == "['list']"


class TestParseRow:
    """Test row parsing."""

    def test_mixed_types(self) -> None:
        row = ["Name", 42, 3.14, True, None]
        result = _parse_row(row)
        assert result == ["Name", 42, 3.14, True, None]

    def test_empty_row(self) -> None:
        assert _parse_row([]) == []


# ============================================================================
# FETCH SPREADSHEET (mocked service)
# ============================================================================

class TestFetchSpreadsheet:
    """Test fetch_spreadsheet with mocked Sheets API."""

    @patch('adapters.sheets.render_charts_as_pngs')
    @patch('adapters.sheets.get_charts_from_spreadsheet')
    @patch('adapters.sheets.get_sheets_service')
    def test_basic_spreadsheet(self, mock_get_service, mock_charts, mock_render) -> None:
        """Fetches metadata + values, assembles SpreadsheetData."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        # API response for spreadsheets().get()
        mock_service.spreadsheets().get().execute.return_value = {
            "spreadsheetId": "sheet123",
            "properties": {
                "title": "Test Spreadsheet",
                "locale": "en_GB",
                "timeZone": "Europe/London",
            },
            "sheets": [
                {"properties": {"sheetId": 0, "title": "Sheet1", "sheetType": "GRID"}},
            ],
        }

        # API response for values().batchGet()
        mock_service.spreadsheets().values().batchGet().execute.return_value = {
            "valueRanges": [
                {"values": [["Name", "Age"], ["Alice", "30"], ["Bob", "25"]]},
            ],
        }

        mock_charts.return_value = []

        with patch('retry.time.sleep'):
            result = fetch_spreadsheet("sheet123")

        assert isinstance(result, SpreadsheetData)
        assert result.title == "Test Spreadsheet"
        assert result.spreadsheet_id == "sheet123"
        assert result.locale == "en_GB"
        assert result.time_zone == "Europe/London"
        assert len(result.sheets) == 1
        assert result.sheets[0].name == "Sheet1"
        assert result.sheets[0].values[0] == ["Name", "Age"]
        assert result.sheets[0].values[1] == ["Alice", "30"]

    @patch('adapters.sheets.render_charts_as_pngs')
    @patch('adapters.sheets.get_charts_from_spreadsheet')
    @patch('adapters.sheets.get_sheets_service')
    def test_mixed_grid_and_object_sheets(self, mock_get_service, mock_charts, mock_render) -> None:
        """OBJECT sheets (chart sheets) get empty values, GRID sheets get data."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        mock_service.spreadsheets().get().execute.return_value = {
            "spreadsheetId": "sheet123",
            "properties": {"title": "Mixed"},
            "sheets": [
                {"properties": {"sheetId": 0, "title": "Data", "sheetType": "GRID"}},
                {"properties": {"sheetId": 1, "title": "Chart1", "sheetType": "OBJECT"}},
            ],
        }

        mock_service.spreadsheets().values().batchGet().execute.return_value = {
            "valueRanges": [
                {"values": [["x", "y"]]},
            ],
        }

        mock_charts.return_value = []

        with patch('retry.time.sleep'):
            result = fetch_spreadsheet("sheet123")

        assert len(result.sheets) == 2
        # GRID sheet has data
        assert result.sheets[0].name == "Data"
        assert result.sheets[0].values == [["x", "y"]]
        assert result.sheets[0].sheet_type == "GRID"
        # OBJECT sheet has empty values
        assert result.sheets[1].name == "Chart1"
        assert result.sheets[1].values == []
        assert result.sheets[1].sheet_type == "OBJECT"

    @patch('adapters.sheets.render_charts_as_pngs')
    @patch('adapters.sheets.get_charts_from_spreadsheet')
    @patch('adapters.sheets.get_sheets_service')
    def test_no_grid_sheets(self, mock_get_service, mock_charts, mock_render) -> None:
        """Spreadsheet with only OBJECT sheets skips batchGet."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        mock_service.spreadsheets().get().execute.return_value = {
            "spreadsheetId": "sheet123",
            "properties": {"title": "Charts Only"},
            "sheets": [
                {"properties": {"sheetId": 0, "title": "Chart1", "sheetType": "OBJECT"}},
            ],
        }

        mock_charts.return_value = []

        with patch('retry.time.sleep'):
            result = fetch_spreadsheet("sheet123")

        # batchGet should NOT have been called
        mock_service.spreadsheets().values().batchGet.assert_not_called()
        assert len(result.sheets) == 1
        assert result.sheets[0].values == []

    @patch('adapters.sheets.render_charts_as_pngs')
    @patch('adapters.sheets.get_charts_from_spreadsheet')
    @patch('adapters.sheets.get_sheets_service')
    def test_charts_rendered_when_present(self, mock_get_service, mock_charts, mock_render) -> None:
        """Charts trigger render_charts_as_pngs when render_charts=True."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        mock_service.spreadsheets().get().execute.return_value = {
            "spreadsheetId": "sheet123",
            "properties": {"title": "With Charts"},
            "sheets": [],
        }

        from models import ChartData
        chart = ChartData(chart_id=1, title="Revenue", chart_type="BAR")
        mock_charts.return_value = [chart]
        mock_render.return_value = ([chart], 150)

        with patch('retry.time.sleep'):
            result = fetch_spreadsheet("sheet123", render_charts=True)

        mock_render.assert_called_once()
        assert result.chart_render_time_ms == 150

    @patch('adapters.sheets.render_charts_as_pngs')
    @patch('adapters.sheets.get_charts_from_spreadsheet')
    @patch('adapters.sheets.get_sheets_service')
    def test_charts_skipped_when_disabled(self, mock_get_service, mock_charts, mock_render) -> None:
        """Charts not rendered when render_charts=False."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        mock_service.spreadsheets().get().execute.return_value = {
            "spreadsheetId": "sheet123",
            "properties": {"title": "No Render"},
            "sheets": [],
        }

        from models import ChartData
        mock_charts.return_value = [ChartData(chart_id=1, title="Chart", chart_type="PIE")]

        with patch('retry.time.sleep'):
            result = fetch_spreadsheet("sheet123", render_charts=False)

        mock_render.assert_not_called()
