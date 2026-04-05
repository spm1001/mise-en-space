"""
Shared test helpers for mise-en-space.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch
from typing import Any


# ============================================================================
# Sheet fetch mock context
# ============================================================================


@dataclass
class SheetMocks:
    """Handles returned by sheet_fetch_context for test customization.

    Customize the sheet data before calling fetch_sheet():
        ctx.sheet_data.charts = [my_chart]
        ctx.sheet_data.formula_count = 47

    Inspect mocks after calling fetch_sheet():
        ctx.mocks["write_manifest"].call_args
        ctx.mocks["write_chart"].assert_called_once()
    """

    sheet_data: MagicMock
    mocks: dict[str, MagicMock] = field(default_factory=dict)


@contextmanager
def sheet_fetch_context(
    tmp_path: Path,
    *,
    content: str = "col1,col2\n1,2",
    comment_count: int = 0,
    tabs_info: list[dict[str, str]] | None = None,
):
    """Context manager that patches all fetch_sheet dependencies.

    Eliminates the 7-9 @patch decorators that every sheet test needs.
    Returns a SheetMocks handle for customizing the SpreadsheetData mock
    and inspecting mock calls after fetch_sheet() runs.

    Usage:
        with sheet_fetch_context(tmp_path) as ctx:
            ctx.sheet_data.formula_count = 47
            result = fetch_sheet("s1", "Sheet", metadata)
            assert result.cues["formula_count"] == 47

    Args:
        tmp_path: pytest tmp_path fixture for deposit folder
        content: extracted CSV content string
        comment_count: number of open comments (0 = no comments)
        tabs_info: per-tab CSV info list (None = empty list)
    """
    # Build the SpreadsheetData mock with sensible defaults
    sheet_data = MagicMock()
    sheet_data.sheets = [MagicMock()]
    sheet_data.charts = []
    sheet_data.warnings = []
    sheet_data.formula_count = 0
    sheet_data.merged_cell_count = 0
    sheet_data.chart_render_time_ms = 0

    comment_content = "comments" if comment_count > 0 else None
    _tabs_info = tabs_info if tabs_info is not None else []

    content_file = tmp_path / "content.csv"
    content_file.write_text(content)

    prefix = "tools.fetch.drive"
    with (
        patch(f"{prefix}.fetch_spreadsheet", return_value=sheet_data) as m_fetch,
        patch(f"{prefix}.extract_sheets_content", return_value=content) as m_extract,
        patch(f"{prefix}.get_deposit_folder", return_value=tmp_path) as m_folder,
        patch(f"{prefix}.write_content", return_value=content_file) as m_write,
        patch(f"{prefix}._enrich_with_comments", return_value=(comment_count, comment_content)) as m_comments,
        patch(f"{prefix}.write_manifest") as m_manifest,
        patch(f"{prefix}._write_per_tab_csvs", return_value=_tabs_info) as m_tabs,
        patch(f"{prefix}.write_chart") as m_chart,
        patch(f"{prefix}.write_charts_metadata") as m_charts_meta,
    ):
        ctx = SheetMocks(
            sheet_data=sheet_data,
            mocks={
                "fetch_spreadsheet": m_fetch,
                "extract_sheets_content": m_extract,
                "get_deposit_folder": m_folder,
                "write_content": m_write,
                "_enrich_with_comments": m_comments,
                "write_manifest": m_manifest,
                "_write_per_tab_csvs": m_tabs,
                "write_chart": m_chart,
                "write_charts_metadata": m_charts_meta,
            },
        )
        yield ctx
