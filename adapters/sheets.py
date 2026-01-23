"""
Sheets adapter — Google Sheets API wrapper.

Fetches spreadsheet metadata and values, assembles SpreadsheetData.
"""

from typing import Any

from models import SpreadsheetData, SheetTab, CellValue
from retry import with_retry
from adapters.services import get_sheets_service


# Fields to request from spreadsheets().get() — only what we need
SPREADSHEET_METADATA_FIELDS = (
    "spreadsheetId,"
    "properties(title,locale,timeZone),"
    "sheets(properties(sheetId,title))"
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
def fetch_spreadsheet(spreadsheet_id: str) -> SpreadsheetData:
    """
    Fetch complete spreadsheet data.

    Calls:
    1. spreadsheets().get() for metadata + sheet list
    2. spreadsheets().values().batchGet() for ALL sheets in one call

    Args:
        spreadsheet_id: The spreadsheet ID (from URL or API)

    Returns:
        SpreadsheetData ready for the extractor

    Raises:
        MiseError: On API failure (converted by @with_retry)
    """
    service = get_sheets_service()

    # Get metadata
    metadata = (
        service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields=SPREADSHEET_METADATA_FIELDS)
        .execute()
    )

    properties = metadata.get("properties", {})
    title = properties.get("title", "Untitled")
    locale = properties.get("locale")
    time_zone = properties.get("timeZone")

    # Get sheet names from metadata
    sheet_names = [
        sheet["properties"]["title"]
        for sheet in metadata.get("sheets", [])
    ]

    # Fetch ALL sheet values in one batch call (not N calls)
    ranges = [f"'{name}'" for name in sheet_names]  # Quote for names with spaces

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

    # Parse batch response - valueRanges is in same order as ranges
    sheets: list[SheetTab] = []
    value_ranges = batch_response.get("valueRanges", [])

    for sheet_name, value_range in zip(sheet_names, value_ranges):
        raw_values = value_range.get("values", [])
        parsed_values = [_parse_row(row) for row in raw_values]
        sheets.append(SheetTab(name=sheet_name, values=parsed_values))

    return SpreadsheetData(
        title=title,
        spreadsheet_id=spreadsheet_id,
        sheets=sheets,
        locale=locale,
        time_zone=time_zone,
    )
