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

    @patch("tools.sheet_edit.update_sheet_values")
    @patch("tools.sheet_edit.clear_sheet_values")
    @patch("tools.sheet_edit.get_sheet_properties")
    def test_tabless_range_does_not_fall_through_to_first_tab(self, mock_props, mock_clear, mock_update) -> None:
        """To Google, a tabless "A1:B2" is valid and means the FIRST tab —
        the exact silent default the multi-tab refusal exists to kill.
        Tab-first parsing reads it as a tab name, fails the lookup, and
        teaches; nothing may be written."""
        mock_props.return_value = self._TABS

        result = sheet_overwrite("s1", "a,b", _META, "A1:B2")

        assert result["error"] is True
        assert "A1:B2" in result["message"]
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


class TestParseCell:
    """[label](url) → rich-text runs; @url → chip; everything else untouched
    (mise-bazuvo)."""

    def test_plain_text_untouched(self) -> None:
        from tools.sheet_edit import _parse_cell
        assert _parse_cell("Widgets, 2024") == ("Widgets, 2024", None, None)

    def test_bare_url_stays_a_url(self) -> None:
        """A bare URL must NOT become a chip — chips replace the cell value
        with the target's title, which would corrupt URL-consuming columns.
        USER_ENTERED auto-links it anyway."""
        from tools.sheet_edit import _parse_cell
        url = "https://drive.google.com/file/d/abc/view"
        assert _parse_cell(url) == (url, None, None)

    def test_single_link_with_surrounding_text(self) -> None:
        from tools.sheet_edit import _parse_cell
        plain, runs, chip = _parse_cell("Debrief: [Nov report](https://x.com/r) (final)")
        assert plain == "Debrief: Nov report (final)"
        assert chip is None
        assert runs == [
            {"startIndex": 9, "format": {"link": {"uri": "https://x.com/r"}}},
            {"startIndex": 19, "format": {}},
        ]

    def test_two_links_one_cell(self) -> None:
        """The multi-artefact index row — the case =HYPERLINK can't do."""
        from tools.sheet_edit import _parse_cell
        plain, runs, chip = _parse_cell("[A](https://x.com/a) · [B](https://x.com/b)")
        assert plain == "A · B"
        assert runs == [
            {"startIndex": 0, "format": {"link": {"uri": "https://x.com/a"}}},
            {"startIndex": 1, "format": {}},
            {"startIndex": 4, "format": {"link": {"uri": "https://x.com/b"}}},
        ]

    def test_link_at_cell_end_has_no_zero_width_run(self) -> None:
        from tools.sheet_edit import _parse_cell
        plain, runs, chip = _parse_cell("See [report](https://x.com/r)")
        assert plain == "See report"
        assert runs[-1] == {"startIndex": 4, "format": {"link": {"uri": "https://x.com/r"}}}

    def test_chip_syntax(self) -> None:
        from tools.sheet_edit import _parse_cell
        plain, runs, chip = _parse_cell("@https://drive.google.com/file/d/abc/view")
        assert plain == "@"
        assert runs is None
        assert chip == "https://drive.google.com/file/d/abc/view"

    def test_non_url_after_at_untouched(self) -> None:
        from tools.sheet_edit import _parse_cell
        assert _parse_cell("@handle") == ("@handle", None, None)


class TestA1Anchor:
    def test_defaults(self) -> None:
        from tools.sheet_edit import _a1_anchor
        assert _a1_anchor(None) == (0, 0)
        assert _a1_anchor("A1") == (0, 0)

    def test_cell_and_range(self) -> None:
        from tools.sheet_edit import _a1_anchor
        assert _a1_anchor("F9") == (8, 5)
        assert _a1_anchor("F9:F15") == (8, 5)
        assert _a1_anchor("AA10") == (9, 26)

    def test_column_only_range(self) -> None:
        from tools.sheet_edit import _a1_anchor
        assert _a1_anchor("F:F") == (0, 5)


class TestSheetOverwriteLinks:
    _TABS = [
        {"sheetId": 0, "title": "Summary", "index": 0},
        {"sheetId": 42, "title": "Costs", "index": 1},
    ]

    @patch("tools.sheet_edit.batch_update")
    @patch("tools.sheet_edit.update_sheet_values")
    @patch("tools.sheet_edit.clear_sheet_values")
    @patch("tools.sheet_edit.get_sheet_properties")
    def test_links_and_chips_overlay_after_grid_write(
        self, mock_props, mock_clear, mock_update, mock_batch,
    ) -> None:
        mock_props.return_value = self._TABS
        mock_update.return_value = 4

        csv_content = ('label,"[A](https://x.com/a) · [B](https://x.com/b)"\n'
                       "plain,@https://drive.google.com/file/d/abc/view")
        result = sheet_overwrite("s1", csv_content, _META, "Costs!F9")

        assert isinstance(result, DoResult)
        # Grid write carries the PLAIN text, anchored at the range
        grid = mock_update.call_args[0][2]
        assert grid == [["label", "A · B"], ["plain", "@"]]
        # Overlay: absolute offsets from the F9 anchor (row 8, col 5)
        reqs = mock_batch.call_args[0][1]
        starts = [q["updateCells"]["start"] for q in reqs]
        assert {"sheetId": 42, "rowIndex": 8, "columnIndex": 6} in starts
        assert {"sheetId": 42, "rowIndex": 9, "columnIndex": 6} in starts
        fields = {q["updateCells"]["fields"] for q in reqs}
        assert fields == {"textFormatRuns", "userEnteredValue,chipRuns"}
        assert result.cues["links_written"] == 1
        assert result.cues["chips_written"] == 1

    @patch("tools.sheet_edit.batch_update")
    @patch("tools.sheet_edit.update_sheet_values")
    @patch("tools.sheet_edit.clear_sheet_values")
    @patch("tools.sheet_edit.get_sheet_properties")
    def test_no_decorations_no_batch_update(
        self, mock_props, mock_clear, mock_update, mock_batch,
    ) -> None:
        mock_props.return_value = [{"sheetId": 0, "title": "Data", "index": 0}]
        mock_update.return_value = 2

        result = sheet_overwrite("s1", "a,b\n1,2", _META)

        assert isinstance(result, DoResult)
        mock_batch.assert_not_called()
        assert "links_written" not in result.cues


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
