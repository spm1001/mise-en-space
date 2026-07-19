"""
Sheet edit operations — overwrite (CSV → first tab) and replace_text
(cell find/replace) for Google Spreadsheets.

Routed here from tools/overwrite.py and tools/edit.py by Spreadsheet MIME.
These ops used to dead-end with "different API path" and no alternative
(mise-lirugi); the spreadsheets write scope was in SCOPES all along, so
this is wiring, not a consent change.

Semantics:
- overwrite: content is parsed as CSV and replaces ALL values on the FIRST
  (grid) tab — clear, then write from A1. Other tabs are untouched (cue
  warning when they exist). Symmetric with do(create, doc_type='sheet'),
  which uploads CSV.
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

logger = logging.getLogger(__name__)


def _quote_tab(title: str) -> str:
    """A1-notation tab quoting: wrap in single quotes, double internal ones."""
    return "'" + title.replace("'", "''") + "'"


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
) -> DoResult | dict[str, Any]:
    """Replace the first tab's values with CSV-parsed content."""
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
        first = min(tabs, key=lambda t: t.get("index", 0))
        tab_ref = _quote_tab(first.get("title", "Sheet1"))
        clear_sheet_values(file_id, tab_ref)
        updated = update_sheet_values(file_id, f"{tab_ref}!A1", rows)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    cues: dict[str, Any] = {
        "tab": first.get("title", ""),
        "rows_written": len(rows),
        "cells_updated": updated,
    }
    if len(tabs) > 1:
        others = [t.get("title", "?") for t in tabs if t is not first]
        cues["warning"] = (
            f"Spreadsheet has {len(tabs)} tabs; overwrite replaced only "
            f"'{first.get('title', '')}'. Untouched: {', '.join(others)}."
        )
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
        cues["warning"] = "Text not found"
    return _sheet_result(file_id, metadata, "replace_text", cues)
