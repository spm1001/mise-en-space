"""
Tests for sheets adapter using mocked HTTP client.

Mocks the sync HTTP client, feeds it API-shaped responses,
and verifies the adapter parses into SpreadsheetData correctly.
"""

import pytest
from unittest.mock import patch, MagicMock, call

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
# FETCH SPREADSHEET (mocked HTTP client)
# ============================================================================

class TestFetchSpreadsheet:
    """Test fetch_spreadsheet with mocked sync HTTP client."""

    @patch('adapters.sheets.render_charts_as_pngs')
    @patch('adapters.sheets.get_charts_from_spreadsheet')
    @patch('adapters.sheets.get_sync_client')
    def test_basic_spreadsheet(self, mock_get_client, mock_charts, mock_render) -> None:
        """Fetches metadata + values, assembles SpreadsheetData."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # First call: metadata, second + third: batchGet (formatted + formula)
        mock_client.get_json.side_effect = [
            {
                "spreadsheetId": "sheet123",
                "properties": {
                    "title": "Test Spreadsheet",
                    "locale": "en_GB",
                    "timeZone": "Europe/London",
                },
                "sheets": [
                    {"properties": {"sheetId": 0, "title": "Sheet1", "sheetType": "GRID"}},
                ],
            },
            {
                "valueRanges": [
                    {"values": [["Name", "Age"], ["Alice", "30"], ["Bob", "25"]]},
                ],
            },
            {
                "valueRanges": [
                    {"values": [["Name", "Age"], ["Alice", "30"], ["Bob", "25"]]},
                ],
            },
        ]

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

        # Verify correct URLs
        calls = mock_client.get_json.call_args_list
        assert "sheet123" in calls[0].args[0]  # metadata
        assert "values:batchGet" in calls[1].args[0]  # formatted values
        assert "values:batchGet" in calls[2].args[0]  # formula values

    @patch('adapters.sheets.render_charts_as_pngs')
    @patch('adapters.sheets.get_charts_from_spreadsheet')
    @patch('adapters.sheets.get_sync_client')
    def test_mixed_grid_and_object_sheets(self, mock_get_client, mock_charts, mock_render) -> None:
        """OBJECT sheets (chart sheets) get empty values, GRID sheets get data."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_json.side_effect = [
            {
                "spreadsheetId": "sheet123",
                "properties": {"title": "Mixed"},
                "sheets": [
                    {"properties": {"sheetId": 0, "title": "Data", "sheetType": "GRID"}},
                    {"properties": {"sheetId": 1, "title": "Chart1", "sheetType": "OBJECT"}},
                ],
            },
            {
                "valueRanges": [
                    {"values": [["x", "y"]]},
                ],
            },
            {
                "valueRanges": [
                    {"values": [["x", "y"]]},
                ],
            },
        ]

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
    @patch('adapters.sheets.get_sync_client')
    def test_no_grid_sheets(self, mock_get_client, mock_charts, mock_render) -> None:
        """Spreadsheet with only OBJECT sheets skips batchGet."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Only one call — metadata. No batchGet since no GRID sheets.
        mock_client.get_json.return_value = {
            "spreadsheetId": "sheet123",
            "properties": {"title": "Charts Only"},
            "sheets": [
                {"properties": {"sheetId": 0, "title": "Chart1", "sheetType": "OBJECT"}},
            ],
        }

        mock_charts.return_value = []

        with patch('retry.time.sleep'):
            result = fetch_spreadsheet("sheet123")

        # Only one get_json call (metadata), no batchGet
        assert mock_client.get_json.call_count == 1
        assert len(result.sheets) == 1
        assert result.sheets[0].values == []

    @patch('adapters.sheets.render_charts_as_pngs')
    @patch('adapters.sheets.get_charts_from_spreadsheet')
    @patch('adapters.sheets.get_sync_client')
    def test_charts_rendered_when_present(self, mock_get_client, mock_charts, mock_render) -> None:
        """Charts trigger render_charts_as_pngs when render_charts=True."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_json.return_value = {
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
    @patch('adapters.sheets.get_sync_client')
    def test_charts_skipped_when_disabled(self, mock_get_client, mock_charts, mock_render) -> None:
        """Charts not rendered when render_charts=False."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_json.return_value = {
            "spreadsheetId": "sheet123",
            "properties": {"title": "No Render"},
            "sheets": [],
        }

        from models import ChartData
        mock_charts.return_value = [ChartData(chart_id=1, title="Chart", chart_type="PIE")]

        with patch('retry.time.sleep'):
            result = fetch_spreadsheet("sheet123", render_charts=False)

        mock_render.assert_not_called()

    @patch('adapters.sheets.render_charts_as_pngs')
    @patch('adapters.sheets.get_charts_from_spreadsheet')
    @patch('adapters.sheets.get_sync_client')
    def test_formula_count(self, mock_get_client, mock_charts, mock_render) -> None:
        """Formula cells are counted from the FORMULA-render batchGet."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_json.side_effect = [
            {
                "spreadsheetId": "sheet123",
                "properties": {"title": "Formulas"},
                "sheets": [
                    {"properties": {"sheetId": 0, "title": "Sheet1", "sheetType": "GRID"}},
                ],
            },
            # FORMATTED_VALUE response
            {
                "valueRanges": [
                    {"values": [["Total", "100"]]},
                ],
            },
            # FORMULA response — one formula cell
            {
                "valueRanges": [
                    {"values": [["Total", "=SUM(A1:A10)"]]},
                ],
            },
        ]

        mock_charts.return_value = []

        with patch('retry.time.sleep'):
            result = fetch_spreadsheet("sheet123")

        assert result.formula_count == 1
