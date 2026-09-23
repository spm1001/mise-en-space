"""
Multi-tab sheet creation, after the CSV upload has made tab 1.

Split from tools/create.py (2026-09-23, mise-suwopu) so the fix could land
without growing a ratchet-frozen module. create.py uploads the first tab's CSV
(Drive's type detection) and hands the new spreadsheet here.

Three things this module guarantees that the old inline version did not:

- Tab 1 is renamed by its REAL sheetId. Drive's CSV import mints it at random
  (380190192 and 2078320758 measured on two creates), so the old rename of
  sheetId 0 400'd every time inside a bare except-pass, and tab 1 kept the
  workbook title (mise-suwopu, mise-natira, mise-taciku).
- Every tab exists before any later tab's values go in, and tab 1 is
  re-entered when it holds formulas. Sheets resolves a formula's sheet
  references at entry time: one naming a tab that does not exist yet is #REF
  and never heals when the tab arrives (mise-taciku).
- cues.tab_names is what a read-back finds, not an echo of the request, and a
  rename failure is a warning, never a silence.
"""

from typing import Any

import httpx

from adapters.sheets import add_sheet, get_sheet_properties, rename_sheet, update_sheet_values
from extractors.sheets import csv_text_to_values
from models import DoResult, MiseError
from tools.sheet_edit import _quote_tab as quote_tab


def finish_multi_tab_sheet(result: DoResult, tabs: list[tuple[str, str]]) -> DoResult:
    """Rename tab 1, add and fill tabs 2..n, re-enter tab 1's formulas, read back.

    The spreadsheet already exists when this runs, so no failure here may turn
    into a bare error: that would orphan a real file whose id the caller never
    sees (essayeur, 2026-09-23). Every step reports into warnings instead, and
    the result always carries the file id.
    """
    spreadsheet_id = result.file_id
    first_tab_name, first_tab_csv = tabs[0]
    warnings: list[str] = []
    step = "reading tab 1's id"
    try:
        first = get_sheet_properties(spreadsheet_id)[0]
        first_title = first.get("title", "")
        step = f"renaming tab 1 to {first_tab_name!r}"
        try:
            rename_sheet(spreadsheet_id, sheet_id=first["sheetId"], new_title=first_tab_name)
            first_title = first_tab_name
        except (MiseError, httpx.HTTPError) as e:
            warnings.append(
                f"Tab 1 could not be renamed to {first_tab_name!r} ({e}); it is named "
                f"{first_title!r}, so formulas naming {first_tab_name!r} will not resolve."
            )
        for tab_name, _ in tabs[1:]:
            step = f"adding tab {tab_name!r}"
            add_sheet(spreadsheet_id, tab_name)
        for tab_name, tab_csv in tabs[1:]:
            step = f"filling tab {tab_name!r}"
            if values := csv_text_to_values(tab_csv):
                update_sheet_values(spreadsheet_id, range_=f"{quote_tab(tab_name)}!A1", values=values)
        first_values = csv_text_to_values(first_tab_csv)
        if any(cell.startswith("=") for row in first_values for cell in row):
            step = "re-entering tab 1's formulas"
            update_sheet_values(spreadsheet_id, range_=f"{quote_tab(first_title)}!A1", values=first_values)
    except (MiseError, httpx.HTTPError) as e:
        warnings.append(
            f"The spreadsheet was created ({spreadsheet_id}) but building it stopped while "
            f"{step}: {e}. It is incomplete; tab_names below says what exists."
        )

    requested = [name for name, _ in tabs]
    cues: dict[str, Any] = result.cues
    try:
        actual = [p.get("title", "") for p in get_sheet_properties(spreadsheet_id)]
    except (MiseError, httpx.HTTPError) as e:
        warnings.append(f"Could not read the tabs back ({e}); tab names are unverified.")
    else:
        if actual != requested:
            warnings.append(f"Tab names on read-back {actual} differ from the requested {requested}.")
        cues["tab_count"] = len(actual)
        cues["tab_names"] = actual
    if warnings:
        cues.setdefault("warnings", []).extend(warnings)
    return result
