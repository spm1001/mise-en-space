"""
Sheet edit operations — overwrite (CSV → first tab) and replace_text
(cell find/replace) for Google Spreadsheets.

Routed here from tools/overwrite.py and tools/edit.py by Spreadsheet MIME.
These ops used to dead-end with "different API path" and no alternative
(mise-lirugi); the spreadsheets write scope was in SCOPES all along, so
this is wiring, not a consent change.

Semantics:
- overwrite without range=: content is parsed as CSV and replaces ALL
  values on the sheet's only tab — clear, then write from A1. Symmetric
  with do(create, doc_type='sheet'), which uploads CSV. On a MULTI-tab
  sheet this REFUSES and teaches range= — an aimless wholesale replace on
  a shared multi-tab sheet is a footgun, not a feature (mise-vadoko).
- overwrite with range=: three grains, all A1 notation. A bare tab name
  ("Costs") clears and replaces that whole tab; a bounded range
  ("Costs!F9:F15") writes exactly those cells, nothing cleared; an anchor
  ("Costs!F9") writes the CSV's shape starting there. USER_ENTERED
  semantics — formulas parse, bare URLs auto-link.
- replace_text: literal substring find/replace across every tab's cell
  values, formulas excluded. Mirrors the plain-file contract.
"""

import csv
import io
import logging
from typing import Any

from adapters.sheets import (
    clear_sheet_values,
    find_replace_cells,
    get_sheet_properties,
    update_sheet_values,
)
from models import DoResult, MiseError
from tools.common import NO_MATCH_WARNING

logger = logging.getLogger(__name__)


def _quote_tab(title: str) -> str:
    """A1-notation tab quoting: wrap in single quotes, double internal ones."""
    return "'" + title.replace("'", "''") + "'"


def _split_range(range_: str) -> tuple[str, str | None]:
    """Split A1 notation into (tab name, cell part or None).

    Accepts quoted tabs ("'Bob''s Tab'!A1"), unquoted ("Costs!F9:F15"),
    and bare tab names ("Costs"). The returned tab name is unquoted with
    doubled apostrophes undone; the cell part is None for a bare tab.
    """
    if range_.startswith("'"):
        i, n = 1, len(range_)
        while i < n:
            if range_[i] == "'":
                if i + 1 < n and range_[i + 1] == "'":
                    i += 2
                    continue
                break
            i += 1
        tab = range_[1:i].replace("''", "'")
        rest = range_[i + 1:]
        return tab, (rest[1:] or None) if rest.startswith("!") else None
    if "!" in range_:
        tab, _, cell = range_.partition("!")
        return tab, cell or None
    return range_, None


def _sheet_result(
    file_id: str, metadata: dict[str, Any], operation: str, cues: dict[str, Any],
) -> DoResult:
    return DoResult(
        file_id=file_id,
        title=metadata.get("name", "Untitled"),
        web_link=metadata.get(
            "webViewLink", f"https://docs.google.com/spreadsheets/d/{file_id}/edit"
        ),
        operation=operation,
        cues=cues,
    )


def sheet_overwrite(
    file_id: str,
    content: str | None,
    metadata: dict[str, Any],
    range_: str | None = None,
) -> DoResult | dict[str, Any]:
    """Write CSV-parsed content to a tab or range (whole-tab when unranged)."""
    if not content:
        return {
            "error": True, "kind": "invalid_input",
            "message": "overwrite on a Spreadsheet takes CSV text via 'content' "
                       "or 'file_path' (a deposit 'source' isn't CSV — read it "
                       "and pass CSV as content=).",
        }

    rows = [r for r in csv.reader(io.StringIO(content))]
    if not rows or not any(any(cell != "" for cell in r) for r in rows):
        return {
            "error": True, "kind": "invalid_input",
            "message": "overwrite on a Spreadsheet: content parsed to zero CSV cells.",
        }

    try:
        tabs = get_sheet_properties(file_id)
        if not tabs:
            return {
                "error": True, "kind": "invalid_input",
                "message": "Spreadsheet has no grid tabs to overwrite.",
            }
        titles = [t.get("title", "") for t in tabs]

        if range_ is None:
            if len(tabs) > 1:
                quoted = ", ".join(f"'{t}'" for t in titles)
                return {
                    "error": True, "kind": "invalid_input",
                    "message": (
                        f"Spreadsheet has {len(tabs)} tabs ({quoted}) — an un-ranged "
                        "overwrite would silently clear and replace only the first. "
                        "Aim it with range=: a bare tab name (range=\"Costs\") clears "
                        "and replaces that whole tab; a bounded range "
                        "(range=\"Costs!F9:F15\") writes exactly those cells; an "
                        "anchor (range=\"Costs!F9\") writes the CSV's shape from there."
                    ),
                }
            only = tabs[0]
            tab_ref = _quote_tab(only.get("title", "Sheet1"))
            clear_sheet_values(file_id, tab_ref)
            updated = update_sheet_values(file_id, f"{tab_ref}!A1", rows)
            cues: dict[str, Any] = {
                "tab": only.get("title", ""),
                "rows_written": len(rows),
                "cells_updated": updated,
            }
            return _sheet_result(file_id, metadata, "overwrite", cues)

        tab_name, cell_part = _split_range(range_)
        match = next((t for t in tabs if t.get("title") == tab_name), None)
        if match is None:  # Sheets' own range parsing is case-insensitive; mirror it
            match = next(
                (t for t in tabs if t.get("title", "").lower() == tab_name.lower()),
                None,
            )
        if match is None:
            quoted = ", ".join(f"'{t}'" for t in titles)
            return {
                "error": True, "kind": "invalid_input",
                "message": f"No tab named '{tab_name}' in this spreadsheet. "
                           f"Tabs: {quoted}.",
            }
        tab_ref = _quote_tab(match.get("title", ""))

        if cell_part is None:
            # Bare tab name = tab-scoped wholesale replace: clear, then write
            clear_sheet_values(file_id, tab_ref)
            updated = update_sheet_values(file_id, f"{tab_ref}!A1", rows)
            cues = {
                "tab": match.get("title", ""),
                "tab_replaced": True,
                "rows_written": len(rows),
                "cells_updated": updated,
            }
        else:
            # Ranged write: nothing cleared, cells outside the write untouched
            updated = update_sheet_values(file_id, f"{tab_ref}!{cell_part}", rows)
            cues = {
                "tab": match.get("title", ""),
                "range": f"{match.get('title', '')}!{cell_part}",
                "rows_written": len(rows),
                "cells_updated": updated,
            }
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    return _sheet_result(file_id, metadata, "overwrite", cues)


def sheet_replace_text(
    file_id: str,
    find: str,
    replace: str,
    metadata: dict[str, Any],
) -> DoResult | dict[str, Any]:
    """Literal find/replace across all tabs' cell values."""
    try:
        count = find_replace_cells(file_id, find, replace)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    cues: dict[str, Any] = {
        "find": find,
        "replace": replace,
        "occurrences_changed": count,
    }
    if count == 0:
        cues["warning"] = NO_MATCH_WARNING
    return _sheet_result(file_id, metadata, "replace_text", cues)
