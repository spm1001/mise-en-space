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
import re
from typing import Any

from adapters.sheets import (
    batch_update,
    clear_sheet_values,
    find_replace_cells,
    get_sheet_properties,
    update_sheet_values,
)
from models import DoResult, MiseError
from tools.common import NO_MATCH_WARNING

logger = logging.getLogger(__name__)

# Link decorations in CSV cell values (mise-bazuvo). [label](url) becomes a
# real rich-text link via textFormatRuns — several per cell work. A whole
# cell of @url becomes a smart chip, which REPLACES the cell text with the
# target's title server-side — that value change is why chips are explicit
# opt-in syntax and a bare URL stays a URL (USER_ENTERED auto-links it).
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_CHIP_RE = re.compile(r"^@(https?://\S+)$")
_A1_CELL_RE = re.compile(r"^([A-Za-z]{1,3})(\d*)")


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


def _a1_anchor(cell_part: str | None) -> tuple[int, int]:
    """Zero-based (row, col) of a range's first cell. None or no digits → A1."""
    if not cell_part:
        return 0, 0
    m = _A1_CELL_RE.match(cell_part)
    if not m:
        return 0, 0
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - ord("A") + 1)
    row = int(m.group(2)) - 1 if m.group(2) else 0
    return max(row, 0), col - 1


def _parse_cell(value: str) -> tuple[str, list[dict[str, Any]] | None, str | None]:
    """Extract link decorations from one CSV cell value.

    Returns (plain_text, text_format_runs, chip_uri). Runs use the plain
    text's indices (markdown syntax stripped); at most one of runs/chip.
    """
    chip = _CHIP_RE.match(value)
    if chip:
        return "@", None, chip.group(1)

    if not _MD_LINK_RE.search(value):
        return value, None, None

    plain: list[str] = []
    runs: list[dict[str, Any]] = []
    pos = 0
    plain_len = 0
    for m in _MD_LINK_RE.finditer(value):
        before = value[pos:m.start()]
        plain.append(before)
        plain_len += len(before)
        label, uri = m.group(1), m.group(2)
        runs.append({"startIndex": plain_len,
                     "format": {"link": {"uri": uri}}})
        plain.append(label)
        plain_len += len(label)
        runs.append({"startIndex": plain_len, "format": {}})
        pos = m.end()
    tail = value[pos:]
    plain.append(tail)
    if not tail and runs and runs[-1]["format"] == {}:
        runs.pop()  # link runs to end of text; a zero-width run would 400
    return "".join(plain), runs, None


def _overlay_requests(
    sheet_id: int,
    row0: int,
    col0: int,
    decorations: dict[tuple[int, int], tuple[list[dict[str, Any]] | None, str | None]],
) -> list[dict[str, Any]]:
    """updateCells requests overlaying links/chips onto already-written cells.

    Link cells touch ONLY textFormatRuns (the value from the grid write
    stands); chip cells rewrite the value too — the '@' placeholder is what
    Google replaces with the target's title.
    """
    requests = []
    for (r, c), (runs, chip_uri) in sorted(decorations.items()):
        if chip_uri:
            cell: dict[str, Any] = {
                "userEnteredValue": {"stringValue": "@"},
                "chipRuns": [{"chip": {"richLinkProperties": {"uri": chip_uri}}}],
            }
            fields = "userEnteredValue,chipRuns"
        else:
            cell = {"textFormatRuns": runs}
            fields = "textFormatRuns"
        requests.append({"updateCells": {
            "start": {"sheetId": sheet_id,
                      "rowIndex": row0 + r, "columnIndex": col0 + c},
            "rows": [{"values": [cell]}],
            "fields": fields,
        }})
    return requests


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

    # Strip link decorations (mise-bazuvo): the plain grid rides values.update
    # so numbers/formulas keep USER_ENTERED parsing; links/chips overlay after.
    plain_rows: list[list[str]] = []
    decorations: dict[tuple[int, int], tuple[list[dict[str, Any]] | None, str | None]] = {}
    for r, row in enumerate(rows):
        prow = []
        for c, cell in enumerate(row):
            plain, runs, chip_uri = _parse_cell(cell)
            prow.append(plain)
            if runs or chip_uri:
                decorations[(r, c)] = (runs, chip_uri)
        plain_rows.append(prow)

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
            target, cell_part = tabs[0], None
            cues: dict[str, Any] = {"tab": target.get("title", "")}
        else:
            tab_name, cell_part = _split_range(range_)
            target = next((t for t in tabs if t.get("title") == tab_name), None)
            if target is None:  # Sheets' own range parsing is case-insensitive
                target = next(
                    (t for t in tabs if t.get("title", "").lower() == tab_name.lower()),
                    None,
                )
            if target is None:
                quoted = ", ".join(f"'{t}'" for t in titles)
                return {
                    "error": True, "kind": "invalid_input",
                    "message": f"No tab named '{tab_name}' in this spreadsheet. "
                               f"Tabs: {quoted}.",
                }
            title = target.get("title", "")
            cues = ({"tab": title, "tab_replaced": True} if cell_part is None
                    else {"tab": title, "range": f"{title}!{cell_part}"})

        tab_ref = _quote_tab(target.get("title", ""))
        if cell_part is None:
            clear_sheet_values(file_id, tab_ref)  # whole-tab replace grain
        write_range = f"{tab_ref}!{cell_part}" if cell_part else f"{tab_ref}!A1"
        updated = update_sheet_values(file_id, write_range, plain_rows)
        cues.update({"rows_written": len(rows), "cells_updated": updated})

        if decorations:
            row0, col0 = _a1_anchor(cell_part)
            batch_update(file_id, _overlay_requests(
                int(target.get("sheetId", 0)), row0, col0, decorations,
            ))
            links = sum(1 for runs, _ in decorations.values() if runs)
            chips = len(decorations) - links
            if links:
                cues["links_written"] = links
            if chips:
                cues["chips_written"] = chips
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
