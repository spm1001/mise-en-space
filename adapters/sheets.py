"""
Sheets adapter — Google Sheets API wrapper.

Fetches spreadsheet metadata, values, and charts. Assembles SpreadsheetData.

Chart rendering uses Slides API (see adapters/charts.py) because Sheets API
has no direct chart export endpoint.

Uses httpx via MiseSyncClient (Phase 1 migration). Will switch to
MiseHttpClient (async) when the tools/server layer goes async.
"""

from typing import Any, Literal

import orjson

from models import SpreadsheetData, SheetTab, ChartData, CellValue
from retry import with_retry
from adapters.http_client import get_sync_client
from adapters.charts import get_charts_from_spreadsheet, render_charts_as_pngs


# Google Sheets API v4 base URL
_SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"

# Fields to request from spreadsheets().get()
# Include sheetType to filter OBJECT sheets, charts for metadata, and merges
SPREADSHEET_METADATA_FIELDS = (
    "spreadsheetId,"
    "properties(title,locale,timeZone),"
    "sheets(properties(sheetId,title,sheetType),"
    "merges,"
    "charts(chartId,spec(title,basicChart(chartType),pieChart,histogramChart)))"
)


def _parse_cell_value(value: Any) -> CellValue:
    """Convert API cell value to our CellValue type."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    # API sometimes returns other types, convert to string
    return str(value)


def _parse_row(row: list[Any]) -> list[CellValue]:
    """Parse a row of cell values."""
    return [_parse_cell_value(v) for v in row]


def _resolve_merges(
    values: list[list[CellValue]],
    merges: list[dict[str, Any]],
) -> int:
    """
    Propagate top-left cell values into empty merged cells.

    Google Sheets API returns values only in the top-left cell of a merge
    range — all other cells come back as empty. This fills them in so CSV
    output has correct data in every row.

    Mutates `values` in place. Returns the number of cells filled.
    """
    filled = 0

    for merge in merges:
        start_row = merge.get("startRowIndex", 0)
        end_row = merge.get("endRowIndex", start_row + 1)
        start_col = merge.get("startColumnIndex", 0)
        end_col = merge.get("endColumnIndex", start_col + 1)

        # Get the top-left cell value (the source of truth)
        if start_row >= len(values):
            continue
        top_row = values[start_row]
        source_value = top_row[start_col] if start_col < len(top_row) else None

        if source_value is None:
            continue

        # Fill all cells in the merge range (skip the top-left itself)
        for row_idx in range(start_row, min(end_row, len(values))):
            row = values[row_idx]
            # Extend row if needed (sparse rows shorter than merge range)
            while len(row) < end_col:
                row.append(None)

            for col_idx in range(start_col, end_col):
                if row_idx == start_row and col_idx == start_col:
                    continue  # Skip the source cell
                if row[col_idx] is None or row[col_idx] == "":
                    row[col_idx] = source_value
                    filled += 1

    return filled


@with_retry(max_attempts=3, delay_ms=1000)
def fetch_spreadsheet(
    spreadsheet_id: str,
    render_charts: bool = True,
    tabs: list[str] | None = None,
) -> SpreadsheetData:
    """
    Fetch complete spreadsheet data including charts.

    Calls:
    1. spreadsheets().get() for metadata + sheet list + chart info
    2. spreadsheets().values().batchGet() for GRID sheets only
    3. Chart rendering via Slides API (if charts present and render_charts=True)

    Args:
        spreadsheet_id: The spreadsheet ID (from URL or API)
        render_charts: Whether to render charts as PNGs (default True)

    Returns:
        SpreadsheetData ready for the extractor

    Raises:
        MiseError: On API failure (converted by @with_retry)
    """
    client = get_sync_client()

    # Get metadata including charts
    metadata = client.get_json(
        f"{_SHEETS_API}/{spreadsheet_id}",
        params={"fields": SPREADSHEET_METADATA_FIELDS},
    )

    properties = metadata.get("properties", {})
    title = properties.get("title", "Untitled")
    locale = properties.get("locale")
    time_zone = properties.get("timeZone")

    # Separate GRID sheets (have values) from OBJECT sheets (chart sheets)
    # Also collect merge ranges per sheet for merged-cell resolution
    grid_sheets: list[tuple[str, str]] = []  # (name, sheetType)
    all_sheet_info: list[tuple[str, str]] = []
    merges_by_sheet: dict[str, list[dict[str, Any]]] = {}  # sheet name → merge ranges

    for sheet in metadata.get("sheets", []):
        props = sheet.get("properties", {})
        name = props.get("title", "")
        sheet_type = props.get("sheetType", "GRID")
        all_sheet_info.append((name, sheet_type))

        # Collect merge ranges for GRID sheets
        sheet_merges = sheet.get("merges", [])
        if sheet_merges:
            merges_by_sheet[name] = sheet_merges

        # Only GRID sheets have values to fetch
        if sheet_type == "GRID":
            grid_sheets.append((name, sheet_type))

    # Filter to requested tabs (if specified)
    warnings: list[str] = []
    if tabs:
        tab_set = set(tabs)
        matched = {name for name, _ in grid_sheets if name in tab_set}
        missing = tab_set - matched
        if missing:
            warnings.append(
                f"Requested tab(s) not found: {', '.join(sorted(missing))}. "
                f"Available: {', '.join(name for name, _ in grid_sheets)}"
            )
        grid_sheets = [(name, st) for name, st in grid_sheets if name in tab_set]
        # Also filter non-GRID sheets
        all_sheet_info = [(name, st) for name, st in all_sheet_info if name in tab_set or st != "GRID"]

    # Fetch values only for GRID sheets
    sheets: list[SheetTab] = []
    formula_count = 0
    merged_cell_count = 0

    if grid_sheets:
        ranges = [f"'{name}'" for name, _ in grid_sheets]

        # batchGet uses repeated "ranges" query params — httpx needs list of tuples
        batch_params: list[tuple[str, str]] = [("valueRenderOption", "FORMATTED_VALUE")]
        batch_params.extend(("ranges", r) for r in ranges)

        batch_response = client.get_json(
            f"{_SHEETS_API}/{spreadsheet_id}/values:batchGet",
            params=batch_params,
        )

        value_ranges = batch_response.get("valueRanges", [])

        # Second batchGet with FORMULA to count formula cells
        formula_params: list[tuple[str, str]] = [("valueRenderOption", "FORMULA")]
        formula_params.extend(("ranges", r) for r in ranges)

        formula_response = client.get_json(
            f"{_SHEETS_API}/{spreadsheet_id}/values:batchGet",
            params=formula_params,
        )
        formula_ranges = formula_response.get("valueRanges", [])

        for (sheet_name, sheet_type), value_range in zip(grid_sheets, value_ranges):
            raw_values = value_range.get("values", [])
            parsed_values = [_parse_row(row) for row in raw_values]

            # Resolve merged cells: propagate top-left value to all covered cells
            if sheet_name in merges_by_sheet:
                merged_cell_count += _resolve_merges(
                    parsed_values, merges_by_sheet[sheet_name]
                )

            sheets.append(SheetTab(
                name=sheet_name,
                values=parsed_values,
                sheet_type=sheet_type,
            ))

        # Count formula cells (cells starting with = in FORMULA render)
        for fr in formula_ranges:
            for row in fr.get("values", []):
                for cell in row:
                    if isinstance(cell, str) and cell.startswith("="):
                        formula_count += 1

    # Add non-GRID sheets (OBJECT sheets = chart sheets) with empty values
    for name, sheet_type in all_sheet_info:
        if sheet_type != "GRID":
            sheets.append(SheetTab(
                name=name,
                values=[],
                sheet_type=sheet_type,
            ))

    # Extract chart metadata
    charts = get_charts_from_spreadsheet(metadata)
    chart_render_time_ms = 0

    # Render charts as PNGs if requested
    if render_charts and charts:
        charts, chart_render_time_ms = render_charts_as_pngs(spreadsheet_id, charts)

    result = SpreadsheetData(
        title=title,
        spreadsheet_id=spreadsheet_id,
        sheets=sheets,
        charts=charts,
        locale=locale,
        time_zone=time_zone,
        chart_render_time_ms=chart_render_time_ms,
        formula_count=formula_count,
        merged_cell_count=merged_cell_count,
    )
    result.warnings.extend(warnings)
    return result


# ---------------------------------------------------------------------------
# Write operations — used by do(operation=create) for multi-tab sheets
# ---------------------------------------------------------------------------


@with_retry(max_attempts=3, delay_ms=1000)
def add_sheet(spreadsheet_id: str, title: str) -> int:
    """
    Add a new sheet tab to an existing spreadsheet.

    Args:
        spreadsheet_id: Target spreadsheet
        title: Name for the new tab

    Returns:
        The new sheet's sheetId (integer)
    """
    client = get_sync_client()
    body = {
        "requests": [
            {"addSheet": {"properties": {"title": title}}}
        ]
    }
    response = client.post_json(
        f"{_SHEETS_API}/{spreadsheet_id}:batchUpdate",
        json_body=body,
    )
    return int(response["replies"][0]["addSheet"]["properties"]["sheetId"])


@with_retry(max_attempts=3, delay_ms=1000)
def update_sheet_values(
    spreadsheet_id: str,
    range_: str,
    values: list[list[CellValue]],
    value_input_option: Literal["RAW", "USER_ENTERED"] = "USER_ENTERED",
) -> int:
    """
    Write values to a sheet range.

    Args:
        spreadsheet_id: Target spreadsheet
        range_: A1 notation range, e.g. "'Tab Name'!A1"
        values: 2D grid of cell values
        value_input_option: RAW (literal) or USER_ENTERED (parses formulae, dates).
            USER_ENTERED preserves =FORMULA cells and auto-detects types.

    Returns:
        Number of cells updated
    """
    client = get_sync_client()
    body = {"values": values}
    # No put_json on client — use request() + orjson directly (same pattern as drive upload)
    response = client.request(
        "PUT",
        f"{_SHEETS_API}/{spreadsheet_id}/values/{range_}",
        params={"valueInputOption": value_input_option},
        json_body=body,
    )
    return int(orjson.loads(response.content).get("updatedCells", 0))


@with_retry(max_attempts=3, delay_ms=1000)
def rename_sheet(spreadsheet_id: str, sheet_id: int, new_title: str) -> None:
    """
    Rename the first sheet (created by CSV upload, defaults to the CSV filename).

    Args:
        spreadsheet_id: Target spreadsheet
        sheet_id: The sheetId to rename (0 for first sheet)
        new_title: New tab name
    """
    client = get_sync_client()
    body = {
        "requests": [
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": sheet_id, "title": new_title},
                    "fields": "title",
                }
            }
        ]
    }
    client.post_json(
        f"{_SHEETS_API}/{spreadsheet_id}:batchUpdate",
        json_body=body,
    )
