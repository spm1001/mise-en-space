# /// script
# requires-python = ">=3.11"
# ///
"""Format renderers for the deposit-format bench (mise-rolira, workstream C).

One canonical table (list of dicts of DISPLAY STRINGS — mise deposits carry
FORMATTED_VALUE, so grouped digits, bracketed negatives and n/m sentinels are
the data, not decoration) rendered into each candidate format. Format is the
ONLY variable: every renderer receives identical rows.

Renderer-specific handling of embedded newlines (the S2 wrapped-cell case) is
deliberate and documented per format — those divergences are the experiment,
not bugs to normalise away:
  aligned   wraps within the column, continuation lines
  tsv       literal two-char \\n escape (line-per-record preserved)
  csv       RFC quoting, real newline inside quotes
  md-min    <br>
  json-min  native \\n escape
"""

from __future__ import annotations

import json

GUTTER = "  "
HR_EVERY = 40  # aligned-hr: restate header every N data rows

FORMATS = ("aligned", "aligned-hr", "tsv", "csv", "md-min", "json-min", "dual")

EXT = {
    "aligned": "txt", "aligned-hr": "txt", "tsv": "tsv", "csv": "csv",
    "md-min": "md", "json-min": "json",
    # dual is a directory recipe, not a single file; see build_dual()
}


def _cell(v: str) -> str:
    return "" if v is None else str(v)


# ── aligned (pdftotext-style whitespace columns) ─────────────────────


def _widths(cols: list[str], rows: list[dict], wrap: dict[str, int] | None) -> dict[str, int]:
    w = {}
    for c in cols:
        longest = max([len(c)] + [max((len(p) for p in _cell(r.get(c)).split("\n")), default=0) for r in rows])
        if wrap and c in wrap:
            longest = min(longest, wrap[c])
        w[c] = longest
    return w


def _wrap_text(text: str, width: int) -> list[str]:
    """Wrap on spaces to width; hard-split tokens longer than width."""
    out = []
    for raw_line in text.split("\n"):
        words, line = raw_line.split(" "), ""
        for word in words:
            while len(word) > width:
                if line:
                    out.append(line)
                    line = ""
                out.append(word[:width])
                word = word[width:]
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                out.append(line)
                line = word
        out.append(line)
    return out or [""]


def render_aligned(cols: list[str], rows: list[dict], numeric_cols: set[str],
                   wrap: dict[str, int] | None = None, header_every: int | None = None) -> str:
    w = _widths(cols, rows, wrap)

    def fmt_line(cells: dict[str, str]) -> str:
        parts = []
        for c in cols:
            v = _cell(cells.get(c))
            parts.append(v.rjust(w[c]) if c in numeric_cols else v.ljust(w[c]))
        return GUTTER.join(parts).rstrip()

    header = fmt_line({c: c for c in cols})
    lines = [header]
    for i, r in enumerate(rows):
        if header_every and i and i % header_every == 0:
            lines += ["", header]
        cell_lines = {c: (_wrap_text(_cell(r.get(c)), w[c]) if (wrap and c in wrap) else [_cell(r.get(c))]) for c in cols}
        height = max(len(v) for v in cell_lines.values())
        for k in range(height):
            lines.append(fmt_line({c: (cell_lines[c][k] if k < len(cell_lines[c]) else "") for c in cols}))
    return "\n".join(lines) + "\n"


# ── delimited ────────────────────────────────────────────────────────


def render_tsv(cols: list[str], rows: list[dict]) -> str:
    def esc(v: str) -> str:
        return _cell(v).replace("\t", " ").replace("\n", "\\n")
    lines = ["\t".join(cols)] + ["\t".join(esc(r.get(c)) for c in cols) for r in rows]
    return "\n".join(lines) + "\n"


def render_csv(cols: list[str], rows: list[dict]) -> str:
    import csv as _csv
    import io
    buf = io.StringIO()
    wtr = _csv.writer(buf, lineterminator="\n")
    wtr.writerow(cols)
    for r in rows:
        wtr.writerow([_cell(r.get(c)) for c in cols])
    return buf.getvalue()


def render_md_min(cols: list[str], rows: list[dict]) -> str:
    def esc(v: str) -> str:
        return _cell(v).replace("|", "\\|").replace("\n", "<br>")
    lines = ["|" + "|".join(cols) + "|", "|" + "|".join("---" for _ in cols) + "|"]
    lines += ["|" + "|".join(esc(r.get(c)) for c in cols) + "|" for r in rows]
    return "\n".join(lines) + "\n"


def render_json_min(cols: list[str], rows: list[dict]) -> str:
    return json.dumps([{c: _cell(r.get(c)) for c in cols} for r in rows],
                      separators=(",", ":"), ensure_ascii=False) + "\n"


# ── dispatch ─────────────────────────────────────────────────────────


def render(fmt: str, cols: list[str], rows: list[dict], numeric_cols: set[str],
           wrap: dict[str, int] | None = None) -> str:
    if fmt == "aligned":
        return render_aligned(cols, rows, numeric_cols, wrap)
    if fmt == "aligned-hr":
        return render_aligned(cols, rows, numeric_cols, wrap, header_every=HR_EVERY)
    if fmt == "tsv":
        return render_tsv(cols, rows)
    if fmt == "csv":
        return render_csv(cols, rows)
    if fmt == "md-min":
        return render_md_min(cols, rows)
    if fmt == "json-min":
        return render_json_min(cols, rows)
    raise ValueError(f"unknown format {fmt!r} (dual is a directory recipe — call build_dual)")


# Eye-level routing note for the dual arm — wording adapted from spec 2's
# winning C2 README (manifest hints were read 15/20 and obeyed 0/10; the
# README scored +40pp routing — routing-findings.md, 2026-08-17).
DUAL_README = """\
# About this deposit

Two forms of the same data, for different jobs:

- `content.txt` — aligned columns, for reading and for quoting rows verbatim.
- `{csv_name}` — the same rows as CSV, for computation: tabs over 500 rows
  should be queried with DuckDB or Polars rather than read into context.

Quote source strings verbatim from `content.txt`.
"""
