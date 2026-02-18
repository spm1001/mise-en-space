"""
Shared test helpers for mise-en-space.

Centralizes mock wiring patterns that repeat across test files.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch, seal
from typing import Any


def mock_api_chain(
    mock_service: MagicMock,
    chain: str,
    response: Any = None,
    *,
    side_effect: Any = None,
) -> MagicMock:
    """Set up a mock Google API response for a chained call.

    Navigates the MagicMock attribute chain and sets return_value (or side_effect)
    on the final method. Returns the final mock method for adding assertions.

    Args:
        mock_service: The mocked service object (from @patch)
        chain: Dot-separated chain. Each part except the last is treated as
               a callable method (traversed via .return_value).
               Examples: "files.get.execute", "users.threads.get.execute",
                         "spreadsheets.values.batchGet.execute"
        response: The return value for the final method
        side_effect: Alternative to response — sets side_effect instead

    Returns:
        The final mock method (for adding assertions like assert_called_once_with)

    Examples:
        # Simple:
        mock_api_chain(service, "files.get.execute", {"id": "f1"})
        # equivalent to: service.files().get().execute.return_value = {"id": "f1"}

        # With assertion:
        execute = mock_api_chain(service, "files.list.execute", {"files": []})
        # ... call adapter ...
        execute.assert_called_once()

        # With side_effect:
        mock_api_chain(service, "files.get.execute", side_effect=HttpError(...))
    """
    parts = chain.split(".")
    obj = mock_service
    for part in parts[:-1]:
        obj = getattr(obj, part).return_value
    final = getattr(obj, parts[-1])
    if side_effect is not None:
        final.side_effect = side_effect
    elif response is not None:
        final.return_value = response
    return final


def seal_service(mock_service: MagicMock) -> None:
    """Seal a mock service after all mock_api_chain() calls.

    Prevents MagicMock from silently creating new attributes when
    production code renames an API method. Without seal, a test passes
    even if the adapter calls files().get_media() but the mock only
    set up files().get() — MagicMock returns a new MagicMock instead
    of raising.

    Must be called AFTER all mock_api_chain() calls for this service.

    Example:
        mock_api_chain(service, "files.get.execute", {"id": "f1"})
        seal_service(service)
        # Now service.files().get().execute() works
        # But service.files().export() raises AttributeError
    """
    seal(mock_service)


def wire_httpx_client(mock_client_cls: MagicMock) -> MagicMock:
    """Wire up httpx.Client context manager mock and return the client instance.

    Replaces the repetitive 3-line pattern:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

    Usage:
        mock_client = wire_httpx_client(mock_client_cls)
    """
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
    return mock_client


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
