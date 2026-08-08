"""Tests for tools/sheet_edit.py — Sheets overwrite/replace_text (mise-lirugi)."""

from unittest.mock import patch, MagicMock

from models import DoResult
from tools.common import NO_MATCH_WARNING
from tools.overwrite import do_overwrite
from tools.edit import do_replace_text
from tools.sheet_edit import sheet_overwrite, sheet_replace_text, _quote_tab

SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_META = {"name": "Budget", "mimeType": SHEET_MIME,
         "webViewLink": "https://docs.google.com/spreadsheets/d/s1/edit"}


class TestQuoteTab:
    def test_simple(self) -> None:
        assert _quote_tab("Sheet1") == "'Sheet1'"

    def test_internal_quote_doubled(self) -> None:
        assert _quote_tab("Bob's Tab") == "'Bob''s Tab'"


class TestSheetOverwrite:
    @patch("tools.sheet_edit.update_sheet_values")
    @patch("tools.sheet_edit.clear_sheet_values")
    @patch("tools.sheet_edit.get_sheet_properties")
    def test_csv_written_to_first_tab(self, mock_props, mock_clear, mock_update) -> None:
        mock_props.return_value = [
            {"sheetId": 0, "title": "Data", "index": 0},
        ]
        mock_update.return_value = 4

        result = sheet_overwrite("s1", "a,b\n1,2", _META)

        assert isinstance(result, DoResult)
        mock_clear.assert_called_once_with("s1", "'Data'")
        args = mock_update.call_args[0]
        assert args[0] == "s1"
        assert args[1] == "'Data'!A1"
        assert args[2] == [["a", "b"], ["1", "2"]]
        assert result.cues["cells_updated"] == 4
        assert "warning" not in result.cues

    @patch("tools.sheet_edit.update_sheet_values")
    @patch("tools.sheet_edit.clear_sheet_values")
    @patch("tools.sheet_edit.get_sheet_properties")
    def test_multi_tab_without_range_refuses(self, mock_props, mock_clear, mock_update) -> None:
        """Contract change (mise-vadoko): the old behaviour silently replaced
        the first tab with a warning cue; on a shared multi-tab sheet that is
        a footgun, so it now refuses and teaches range=."""
        mock_props.return_value = [
            {"sheetId": 7, "title": "Later", "index": 1},
            {"sheetId": 0, "title": "First", "index": 0},
        ]

        result = sheet_overwrite("s1", "x,y", _META)

        assert result["error"] is True
        assert "range=" in result["message"]
        assert "Later" in result["message"] and "First" in result["message"]
        mock_clear.assert_not_called()
        mock_update.assert_not_called()

    def test_empty_content_rejected(self) -> None:
        result = sheet_overwrite("s1", "", _META)
        assert result["error"] is True

        result = sheet_overwrite("s1", "\n\n", _META)
        assert result["error"] is True
        assert "zero CSV cells" in result["message"]


class TestSheetOverwriteRange:
    """range= on a Sheets overwrite (mise-vadoko): bounded range, anchor,
    and bare-tab grains, with other tabs never touched."""

    _TABS = [
        {"sheetId": 0, "title": "Summary", "index": 0},
        {"sheetId": 7, "title": "Costs", "index": 1},
    ]

    @patch("tools.sheet_edit.update_sheet_values")
    @patch("tools.sheet_edit.clear_sheet_values")
    @patch("tools.sheet_edit.get_sheet_properties")
    def test_bounded_range_writes_without_clearing(self, mock_props, mock_clear, mock_update) -> None:
        mock_props.return_value = self._TABS
        mock_update.return_value = 7

        result = sheet_overwrite("s1", "1\n2\n3\n4\n5\n6\n7", _META, "Costs!F9:F15")

        assert isinstance(result, DoResult)
        mock_clear.assert_not_called()
        mock_update.assert_called_once()
        assert mock_update.call_args[0][1] == "'Costs'!F9:F15"
        assert result.cues["range"] == "Costs!F9:F15"
        assert result.cues["cells_updated"] == 7

    @patch("tools.sheet_edit.update_sheet_values")
    @patch("tools.sheet_edit.clear_sheet_values")
    @patch("tools.sheet_edit.get_sheet_properties")
    def test_bare_tab_name_clears_then_replaces_that_tab(self, mock_props, mock_clear, mock_update) -> None:
        mock_props.return_value = self._TABS
        mock_update.return_value = 2

        result = sheet_overwrite("s1", "a,b", _META, "Costs")

        assert isinstance(result, DoResult)
        mock_clear.assert_called_once_with("s1", "'Costs'")
        assert mock_update.call_args[0][1] == "'Costs'!A1"
        assert result.cues["tab_replaced"] is True

    @patch("tools.sheet_edit.update_sheet_values")
    @patch("tools.sheet_edit.clear_sheet_values")
    @patch("tools.sheet_edit.get_sheet_properties")
    def test_quoted_tab_with_apostrophe(self, mock_props, mock_clear, mock_update) -> None:
        mock_props.return_value = [{"sheetId": 0, "title": "Bob's Tab", "index": 0}]
        mock_update.return_value = 1

        result = sheet_overwrite("s1", "v", _META, "'Bob''s Tab'!B2")

        assert isinstance(result, DoResult)
        mock_clear.assert_not_called()
        assert mock_update.call_args[0][1] == "'Bob''s Tab'!B2"

    @patch("tools.sheet_edit.update_sheet_values")
    @patch("tools.sheet_edit.clear_sheet_values")
    @patch("tools.sheet_edit.get_sheet_properties")
    def test_tab_match_is_case_insensitive(self, mock_props, mock_clear, mock_update) -> None:
        """Sheets' own range parsing is case-insensitive; the canonical
        title from properties is what goes on the wire."""
        mock_props.return_value = self._TABS
        mock_update.return_value = 1

        result = sheet_overwrite("s1", "v", _META, "costs!A1")

        assert isinstance(result, DoResult)
        assert mock_update.call_args[0][1] == "'Costs'!A1"

    @patch("tools.sheet_edit.update_sheet_values")
    @patch("tools.sheet_edit.clear_sheet_values")
    @patch("tools.sheet_edit.get_sheet_properties")
    def test_unknown_tab_names_available_tabs(self, mock_props, mock_clear, mock_update) -> None:
        mock_props.return_value = self._TABS

        result = sheet_overwrite("s1", "v", _META, "Ghost!A1")

        assert result["error"] is True
        assert "Ghost" in result["message"]
        assert "Summary" in result["message"] and "Costs" in result["message"]
        mock_clear.assert_not_called()
        mock_update.assert_not_called()

    def test_range_on_non_sheet_rejected(self) -> None:
        doc_meta = {"name": "D", "mimeType": "application/vnd.google-apps.document"}
        result = do_overwrite(
            file_id="a" * 20, content="x", metadata=doc_meta, range_="Costs!A1",
        )
        assert result["error"] is True
        assert "spreadsheets" in result["message"]


class TestSplitRange:
    def test_bare_tab(self) -> None:
        from tools.sheet_edit import _split_range
        assert _split_range("Costs") == ("Costs", None)

    def test_unquoted_with_cells(self) -> None:
        from tools.sheet_edit import _split_range
        assert _split_range("Costs!F9:F15") == ("Costs", "F9:F15")

    def test_quoted_with_apostrophe_and_cells(self) -> None:
        from tools.sheet_edit import _split_range
        assert _split_range("'Bob''s Tab'!A1") == ("Bob's Tab", "A1")

    def test_quoted_bare_tab(self) -> None:
        from tools.sheet_edit import _split_range
        assert _split_range("'Two Words'") == ("Two Words", None)


class TestSheetReplaceText:
    @patch("tools.sheet_edit.find_replace_cells")
    def test_occurrences_in_cues(self, mock_fr) -> None:
        mock_fr.return_value = 3
        result = sheet_replace_text("s1", "old-name", "new-name", _META)
        assert isinstance(result, DoResult)
        mock_fr.assert_called_once_with("s1", "old-name", "new-name")
        assert result.cues["occurrences_changed"] == 3
        assert "warning" not in result.cues

    @patch("tools.sheet_edit.find_replace_cells")
    def test_zero_occurrences_warns(self, mock_fr) -> None:
        mock_fr.return_value = 0
        result = sheet_replace_text("s1", "ghost", "x", _META)
        assert isinstance(result, DoResult)
        assert result.cues["warning"] == NO_MATCH_WARNING


class TestRouting:
    """The 2026-07-10 dead-end: sheet MIME must route to the Sheets path,
    never to plain_file's 'different API path' rejection."""

    @patch("tools.sheet_edit.update_sheet_values")
    @patch("tools.sheet_edit.clear_sheet_values")
    @patch("tools.sheet_edit.get_sheet_properties")
    def test_overwrite_routes_sheets(self, mock_props, mock_clear, mock_update) -> None:
        mock_props.return_value = [{"sheetId": 0, "title": "S", "index": 0}]
        mock_update.return_value = 1
        result = do_overwrite(file_id="a" * 20, content="v", metadata=_META)
        assert isinstance(result, DoResult)
        assert mock_update.called

    @patch("tools.sheet_edit.find_replace_cells")
    def test_replace_text_routes_sheets(self, mock_fr) -> None:
        mock_fr.return_value = 1
        result = do_replace_text(
            file_id="a" * 20, find="x", content="y", metadata=_META,
        )
        assert isinstance(result, DoResult)
        assert mock_fr.called

    def test_overwrite_source_only_names_remedy(self, tmp_path) -> None:
        # A sheet deposit isn't CSV — source-only gets a teaching error
        (tmp_path / "content.md").write_text("| a |")
        result = do_overwrite(
            file_id="a" * 20, source=str(tmp_path),
            base_path=str(tmp_path.parent), metadata=_META,
        )
        assert result["error"] is True
        assert "CSV" in result["message"]

    def test_prepend_rejection_names_alternatives(self) -> None:
        from tools.edit import do_prepend
        result = do_prepend(file_id="a" * 20, content="x", metadata=_META)
        assert result["error"] is True
        assert "overwrite" in result["message"]
        assert "replace_text" in result["message"]
