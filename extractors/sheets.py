"""
Sheets Extractor — Pure function for converting spreadsheet data to CSV text.

Receives pre-assembled spreadsheet data, returns multi-sheet CSV output.
No API calls, no MCP awareness.
"""

import csv
import io
import re

from models import SpreadsheetData, CellValue

# The banner extract_sheets_content writes above each tab's rows. strip_sheet_header
# is its inverse and MUST match what the writer emits — the round-trip test in
# tests/unit/test_sheets.py holds the two together, so a format change here reddens
# there rather than silently re-breaking create-from-deposit (mise-kacani).
_SHEET_HEADER_RE = re.compile(r"^=== Sheet: .* ===\r?\n?")


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


def extract_sheets_per_tab(
    data: SpreadsheetData,
) -> list[tuple[str, str]]:
    """
    Extract each sheet tab as a separate CSV string.

    Returns:
        List of (tab_name, csv_content) tuples.
        Empty sheets are included with "(empty)" content.
        Populates data.warnings (same as extract_sheets_content).
    """
    data.warnings = []
    result: list[tuple[str, str]] = []
    empty_sheets: list[str] = []

    for sheet in data.sheets:
        if sheet.values:
            lines = [_row_to_csv(row) for row in sheet.values]
            result.append((sheet.name, "\n".join(lines) + "\n"))
        else:
            result.append((sheet.name, "(empty)\n"))
            empty_sheets.append(sheet.name)

    if empty_sheets:
        if len(empty_sheets) == 1:
            data.warnings.append(f"Sheet '{empty_sheets[0]}' is empty")
        else:
            data.warnings.append(
                f"{len(empty_sheets)} sheets are empty: {', '.join(empty_sheets)}"
            )

    return result


def strip_sheet_header(csv_text: str) -> str:
    """
    Remove the leading '=== Sheet: X ===' banner from deposit CSV text.

    A single-tab deposit's content.csv opens with the banner (the manifest's one
    tab entry points at that file), so create-from-deposit must strip it or the
    banner lands as row 1 of the new spreadsheet. Headerless per-tab files pass
    through untouched, as does a banner-shaped line anywhere past the first —
    only position 0 is a header. The extractor's '(empty)' placeholder for an
    empty tab normalises to '' so it can't become a data cell either.
    """
    stripped = _SHEET_HEADER_RE.sub("", csv_text.lstrip("\n"), count=1)
    if stripped.strip() == "(empty)":
        return ""
    return stripped


def csv_text_to_values(csv_text: str) -> list[list[str]]:
    """Parse CSV text into a 2D list of strings for the Sheets values API."""
    reader = csv.reader(io.StringIO(csv_text))
    return [row for row in reader]


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
