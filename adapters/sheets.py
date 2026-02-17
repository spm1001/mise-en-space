"""
Sheets adapter — Google Sheets API wrapper.

Fetches spreadsheet metadata, values, and charts. Assembles SpreadsheetData.

Chart rendering uses Slides API (see adapters/charts.py) because Sheets API
has no direct chart export endpoint.
"""

from typing import Any, Literal

from models import SpreadsheetData, SheetTab, ChartData, CellValue
from retry import with_retry
from adapters.services import get_sheets_service
from adapters.charts import get_charts_from_spreadsheet, render_charts_as_pngs


# Fields to request from spreadsheets().get()
# Include sheetType to filter OBJECT sheets, and charts for metadata
SPREADSHEET_METADATA_FIELDS = (
    "spreadsheetId,"
    "properties(title,locale,timeZone),"
    "sheets(properties(sheetId,title,sheetType),"
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


@with_retry(max_attempts=3, delay_ms=1000)
def fetch_spreadsheet(
    spreadsheet_id: str,
    render_charts: bool = True,
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
    service = get_sheets_service()

    # Get metadata including charts
    metadata = (
        service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields=SPREADSHEET_METADATA_FIELDS)
        .execute()
    )

    properties = metadata.get("properties", {})
    title = properties.get("title", "Untitled")
    locale = properties.get("locale")
    time_zone = properties.get("timeZone")

    # Separate GRID sheets (have values) from OBJECT sheets (chart sheets)
    grid_sheets: list[tuple[str, str]] = []  # (name, sheetType)
    all_sheet_info: list[tuple[str, str]] = []

    for sheet in metadata.get("sheets", []):
        props = sheet.get("properties", {})
        name = props.get("title", "")
        sheet_type = props.get("sheetType", "GRID")
        all_sheet_info.append((name, sheet_type))

        # Only GRID sheets have values to fetch
        if sheet_type == "GRID":
            grid_sheets.append((name, sheet_type))

    # Fetch values only for GRID sheets
    sheets: list[SheetTab] = []
    formula_count = 0

    if grid_sheets:
        ranges = [f"'{name}'" for name, _ in grid_sheets]

        batch_response = (
            service.spreadsheets()
            .values()
            .batchGet(
                spreadsheetId=spreadsheet_id,
                ranges=ranges,
                valueRenderOption="FORMATTED_VALUE",
            )
            .execute()
        )

        value_ranges = batch_response.get("valueRanges", [])

        # Second batchGet with FORMULA to count formula cells
        formula_response = (
            service.spreadsheets()
            .values()
            .batchGet(
                spreadsheetId=spreadsheet_id,
                ranges=ranges,
                valueRenderOption="FORMULA",
            )
            .execute()
        )
        formula_ranges = formula_response.get("valueRanges", [])

        for (sheet_name, sheet_type), value_range in zip(grid_sheets, value_ranges):
            raw_values = value_range.get("values", [])
            parsed_values = [_parse_row(row) for row in raw_values]
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

    return SpreadsheetData(
        title=title,
        spreadsheet_id=spreadsheet_id,
        sheets=sheets,
        charts=charts,
        locale=locale,
        time_zone=time_zone,
        chart_render_time_ms=chart_render_time_ms,
        formula_count=formula_count,
    )


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
    service = get_sheets_service()
    body = {
        "requests": [
            {"addSheet": {"properties": {"title": title}}}
        ]
    }
    response = (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        .execute()
    )
    return response["replies"][0]["addSheet"]["properties"]["sheetId"]


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
    service = get_sheets_service()
    body = {"values": values}
    response = (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=range_,
            valueInputOption=value_input_option,
            body=body,
        )
        .execute()
    )
    return response.get("updatedCells", 0)


@with_retry(max_attempts=3, delay_ms=1000)
def rename_sheet(spreadsheet_id: str, sheet_id: int, new_title: str) -> None:
    """
    Rename the first sheet (created by CSV upload, defaults to the CSV filename).

    Args:
        spreadsheet_id: Target spreadsheet
        sheet_id: The sheetId to rename (0 for first sheet)
        new_title: New tab name
    """
    service = get_sheets_service()
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
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body=body
    ).execute()
