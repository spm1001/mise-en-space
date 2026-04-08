"""
Tests for sheets adapter using mocked HTTP client.

Mocks the sync HTTP client, feeds it API-shaped responses,
and verifies the adapter parses into SpreadsheetData correctly.
"""

import pytest
from unittest.mock import patch, MagicMock, call

from models import SpreadsheetData
from adapters.sheets import fetch_spreadsheet, _parse_cell_value, _parse_row, _resolve_merges


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
# MERGE RESOLUTION
# ============================================================================


class TestResolveMerges:
    """Test merged cell value propagation."""

    def test_vertical_merge(self) -> None:
        """Vertical merge propagates top-left value down empty rows."""
        values = [
            ["Region", "Revenue"],
            [None, "100"],
            [None, "200"],
            ["Other", "300"],
        ]
        merge = {
            "startRowIndex": 0, "endRowIndex": 3,
            "startColumnIndex": 0, "endColumnIndex": 1,
        }
        filled = _resolve_merges(values, [merge])

        assert values[1][0] == "Region"
        assert values[2][0] == "Region"
        assert values[3][0] == "Other"  # Not in merge range
        assert filled == 2

    def test_horizontal_merge(self) -> None:
        """Horizontal merge propagates value across columns."""
        values = [
            ["Header", None, None],
            ["a", "b", "c"],
        ]
        merge = {
            "startRowIndex": 0, "endRowIndex": 1,
            "startColumnIndex": 0, "endColumnIndex": 3,
        }
        filled = _resolve_merges(values, [merge])

        assert values[0] == ["Header", "Header", "Header"]
        assert filled == 2

    def test_block_merge(self) -> None:
        """2x2 block merge fills all cells from top-left."""
        values = [
            ["Total", None],
            [None, None],
        ]
        merge = {
            "startRowIndex": 0, "endRowIndex": 2,
            "startColumnIndex": 0, "endColumnIndex": 2,
        }
        filled = _resolve_merges(values, [merge])

        assert values[0] == ["Total", "Total"]
        assert values[1] == ["Total", "Total"]
        assert filled == 3

    def test_no_merges(self) -> None:
        """No merges returns zero."""
        values = [["a", "b"], ["c", "d"]]
        filled = _resolve_merges(values, [])
        assert filled == 0

    def test_merge_beyond_data(self) -> None:
        """Merge referencing rows beyond data is safely skipped."""
        values = [["a"]]
        merge = {
            "startRowIndex": 5, "endRowIndex": 7,
            "startColumnIndex": 0, "endColumnIndex": 1,
        }
        filled = _resolve_merges(values, [merge])
        assert filled == 0

    def test_sparse_row_extended(self) -> None:
        """Short rows are extended when merge range exceeds row length."""
        values = [
            ["X"],
            [],
        ]
        merge = {
            "startRowIndex": 0, "endRowIndex": 2,
            "startColumnIndex": 0, "endColumnIndex": 1,
        }
        filled = _resolve_merges(values, [merge])

        assert values[1][0] == "X"
        assert filled == 1

    def test_source_value_none_skipped(self) -> None:
        """Merge with None source value doesn't fill anything."""
        values = [
            [None, "ok"],
            [None, "fine"],
        ]
        merge = {
            "startRowIndex": 0, "endRowIndex": 2,
            "startColumnIndex": 0, "endColumnIndex": 1,
        }
        filled = _resolve_merges(values, [merge])
        assert filled == 0

    def test_multiple_merges(self) -> None:
        """Multiple independent merges are all resolved."""
        values = [
            ["A", "B", "X"],
            [None, None, "Y"],
        ]
        merges = [
            {"startRowIndex": 0, "endRowIndex": 2,
             "startColumnIndex": 0, "endColumnIndex": 1},
            {"startRowIndex": 0, "endRowIndex": 2,
             "startColumnIndex": 1, "endColumnIndex": 2},
        ]
        filled = _resolve_merges(values, merges)

        assert values[1][0] == "A"
        assert values[1][1] == "B"
        assert values[1][2] == "Y"  # Not in any merge
        assert filled == 2


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


# ============================================================================
# TAB FILTERING
# ============================================================================

class TestTabFiltering:
    """Test tabs parameter filters which sheets are fetched."""

    def _make_multi_tab_metadata(self) -> dict:
        return {
            "spreadsheetId": "sheet123",
            "properties": {"title": "Multi-Tab"},
            "sheets": [
                {"properties": {"sheetId": 0, "title": "Overview", "sheetType": "GRID"}},
                {"properties": {"sheetId": 1, "title": "Details", "sheetType": "GRID"}},
                {"properties": {"sheetId": 2, "title": "Archive", "sheetType": "GRID"}},
            ],
        }

    @patch('adapters.sheets.render_charts_as_pngs')
    @patch('adapters.sheets.get_charts_from_spreadsheet')
    @patch('adapters.sheets.get_sync_client')
    def test_tabs_filter_selects_only_named_tabs(self, mock_get_client, mock_charts, mock_render) -> None:
        """Only requested tabs are fetched and returned."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_json.side_effect = [
            self._make_multi_tab_metadata(),
            # batchGet FORMATTED_VALUE — only 2 tabs requested
            {"valueRanges": [
                {"values": [["A", "B"]]},
                {"values": [["X", "Y"]]},
            ]},
            # batchGet FORMULA
            {"valueRanges": [
                {"values": [["A", "B"]]},
                {"values": [["X", "Y"]]},
            ]},
        ]

        mock_charts.return_value = []

        with patch('retry.time.sleep'):
            result = fetch_spreadsheet("sheet123", tabs=["Overview", "Details"])

        assert len(result.sheets) == 2
        assert result.sheets[0].name == "Overview"
        assert result.sheets[1].name == "Details"

        # Verify batchGet only requested 2 ranges
        batch_call = mock_client.get_json.call_args_list[1]
        ranges_in_call = [v for k, v in batch_call.kwargs.get("params", batch_call[1].get("params", [])) if k == "ranges"]
        assert len(ranges_in_call) == 2

    @patch('adapters.sheets.render_charts_as_pngs')
    @patch('adapters.sheets.get_charts_from_spreadsheet')
    @patch('adapters.sheets.get_sync_client')
    def test_tabs_filter_warns_on_missing_tab(self, mock_get_client, mock_charts, mock_render) -> None:
        """Requesting a non-existent tab adds a warning."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_json.side_effect = [
            self._make_multi_tab_metadata(),
            {"valueRanges": [{"values": [["A"]]}]},
            {"valueRanges": [{"values": [["A"]]}]},
        ]

        mock_charts.return_value = []

        with patch('retry.time.sleep'):
            result = fetch_spreadsheet("sheet123", tabs=["Overview", "Nonexistent"])

        assert len(result.sheets) == 1
        assert result.sheets[0].name == "Overview"
        assert any("Nonexistent" in w for w in result.warnings)

    @patch('adapters.sheets.render_charts_as_pngs')
    @patch('adapters.sheets.get_charts_from_spreadsheet')
    @patch('adapters.sheets.get_sync_client')
    def test_tabs_none_fetches_all(self, mock_get_client, mock_charts, mock_render) -> None:
        """Default tabs=None fetches all tabs (no change in behaviour)."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_json.side_effect = [
            self._make_multi_tab_metadata(),
            {"valueRanges": [
                {"values": [["A"]]},
                {"values": [["B"]]},
                {"values": [["C"]]},
            ]},
            {"valueRanges": [
                {"values": [["A"]]},
                {"values": [["B"]]},
                {"values": [["C"]]},
            ]},
        ]

        mock_charts.return_value = []

        with patch('retry.time.sleep'):
            result = fetch_spreadsheet("sheet123", tabs=None)

        assert len(result.sheets) == 3
