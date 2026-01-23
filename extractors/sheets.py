"""
Sheets Extractor — Pure function for converting spreadsheet data to CSV text.

Receives pre-assembled spreadsheet data, returns multi-sheet CSV output.
No API calls, no MCP awareness.
"""

from models import SpreadsheetData, CellValue


def extract_sheets_content(
    data: SpreadsheetData,
    max_length: int | None = None,
) -> str:
    """
    Convert spreadsheet data to CSV text with sheet headers.

    Populates data.warnings with extraction issues encountered.

    Args:
        data: SpreadsheetData with title and sheets
        max_length: Optional character limit. Truncates if exceeded.

    Returns:
        CSV text with sheet headers like:
            === Sheet: Summary ===
            Name,Value,Date
            Revenue,1000000,2024-01-01
            ...

            === Sheet: Details ===
            ID,Description
            1,Widget A
    """
    content_parts: list[str] = []
    total_length = 0
    empty_sheets: list[str] = []

    # Clear any existing warnings
    data.warnings = []

    for sheet in data.sheets:
        sheet_name = sheet.name
        values = sheet.values

        if values:
            sheet_content = f"\n=== Sheet: {sheet_name} ===\n"

            for row in values:
                csv_row = _row_to_csv(row)
                sheet_content += csv_row + "\n"

            # Check length limit
            if max_length and (total_length + len(sheet_content)) > max_length:
                remaining = max_length - total_length
                if remaining > 100:
                    content_parts.append(sheet_content[:remaining])
                    content_parts.append(f"\n[... TRUNCATED at {max_length:,} chars ...]")
                data.warnings.append(f"Content truncated at {max_length:,} characters")
                break

            content_parts.append(sheet_content)
            total_length += len(sheet_content)
        else:
            content_parts.append(f"\n=== Sheet: {sheet_name} ===\n(empty)\n")
            empty_sheets.append(sheet_name)

    # Warn about empty sheets
    if empty_sheets:
        if len(empty_sheets) == 1:
            data.warnings.append(f"Sheet '{empty_sheets[0]}' is empty")
        else:
            data.warnings.append(f"{len(empty_sheets)} sheets are empty: {', '.join(empty_sheets)}")

    return "".join(content_parts).strip()


def _row_to_csv(row: list[CellValue]) -> str:
    """
    Convert a row of cells to CSV format with proper escaping.

    Escapes cells containing commas, quotes, or newlines.
    """
    csv_cells: list[str] = []

    for cell in row:
        cell_str = str(cell) if cell is not None else ""

        # Escape if contains special characters
        if "," in cell_str or '"' in cell_str or "\n" in cell_str:
            # Double any quotes, wrap in quotes
            escaped = cell_str.replace('"', '""')
            csv_cells.append(f'"{escaped}"')
        else:
            csv_cells.append(cell_str)

    return ",".join(csv_cells)
