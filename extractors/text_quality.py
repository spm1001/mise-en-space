"""
Text-structure quality heuristics for converted documents.

Judges whether a converter's text output silently lost table structure.
Two failure signatures, one detector (their union — mise-columi):

- **Split-flattening**: every cell lands on its own line. Looks broken,
  and the ratio signals (short/sentence/numeric) catch it.
- **Delimiter-stripping**: a table row survives on ONE line with its
  delimiters gone, so it reads as ordinary prose made of numbers. Looks
  fine, which is exactly the danger — the numeric-row signal catches it.

Pure functions, no I/O (extractors layer). Used by adapters/pdf.py to
decide whether markitdown output is trustworthy or the conversion should
fall back to Drive.
"""

import logging
import re

log = logging.getLogger(__name__)

_FLAT_MIN_LINES = 20
_FLAT_SHORT_RATIO = 0.60   # lines with 1-3 tokens
_FLAT_SENTENCE_RATIO = 0.10  # lines with 6+ tokens
_FLAT_NUMERIC_RATIO = 0.15   # lines containing digits

# A "bare numeric token" — the kind that fills table cells: optional money/
# paren-negative decoration, at most one decimal point. "3,214", "£1.2",
# "45%", "(2.1)" yes; "3.4.1" (TOC section), "£3,214m" (prose unit) no —
# prose spells figures with unit suffixes, tables leave them bare, and that
# distinction is what kept the bakeoff false-positive-free (mise-columi).
_NUMERIC_TOKEN_RE = re.compile(r"[£$€(]?[\d,]+(?:\.\d+)?[%)]?")

# Undecorated figures only — no paren or currency forms. Runs of these are
# the table fingerprint; decorated numbers also run in address directories
# ("(26) 321 ... CA 90212"), which is why runs exclude them (mise-hasati).
_BARE_NUMERIC_RE = re.compile(r"[\d,]+(?:\.\d+)?%?")

# En-dash and friends fill empty cells in financial tables — at a row's
# tail a nil marker is a cell value, not punctuation.
_NIL_TOKENS = frozenset({"–", "—", "-", "n/a"})

_ROW_MIN_NUMERICS = 3   # bakeoff-validated: >=3 bare numerics on one line
_ROW_MIN_COUNT = 3      # a table implies several rows; one line proves nothing
_ROW_MIN_RUN = 4        # consecutive bare figures prose essentially never makes


def _is_numeric_token(token: str) -> bool:
    return bool(_NUMERIC_TOKEN_RE.fullmatch(token))


def _is_data_token(token: str) -> bool:
    return _is_numeric_token(token) or token.lower() in _NIL_TOKENS


def _is_run_token(token: str) -> bool:
    return bool(_BARE_NUMERIC_RE.fullmatch(token)) or token.lower() in _NIL_TOKENS


def _is_stripped_row(line: str) -> bool:
    """One line that is a delimiter-stripped table row.

    Gate: >=3 numeric tokens (TOC lines like "1.2 Overview 14" fail here).
    Then either signal fires:

    - **end-cluster** — the line ends with >=2 consecutive data tokens
      (numeric or nil): data rows right-align their figures, and "… 13
      140 –" ends in a nil *cell*, while prose quoting numbers interleaves
      words ("grew from 1,200 to 1,500 in 2024" ends word-then-number).
    - **figure run** — >=4 consecutive bare-numeric/nil tokens anywhere:
      the stripped cell sequence itself, which survives when a two-column
      layout bleeds prose onto the row's tail ("Males 27.7 22.9 27.1 22.3
      swap had a nil valuation…") or the row ends in vesting dates.

    Recalibrated against the real ITV FY2025 annual report (mise-hasati):
    on table-dense pages the end-cluster alone caught 75-84%, both signals
    together 86-100%, with zero prose/TOC/address-directory false fires.
    """
    if "|" in line:
        return False
    tokens = line.split()
    numerics = sum(1 for t in tokens if _is_numeric_token(t))
    if numerics < _ROW_MIN_NUMERICS:
        return False
    if len(tokens) >= 2 and _is_data_token(tokens[-1]) and _is_data_token(tokens[-2]):
        return True
    run = 0
    for token in tokens:
        run = run + 1 if _is_run_token(token) else 0
        if run >= _ROW_MIN_RUN:
            return True
    return False


def looks_like_flattened_tables(content: str) -> bool:
    """
    Detect converter output that silently lost table structure.

    Union of two signals (they catch different failures):
    - ratio signal — split-flattening: 60%+ lines of 1-3 tokens, <10%
      sentence-length lines, 15%+ lines with digits; needs >=20 lines.
    - stripped-row signal — delimiter-stripping: >=3 lines that each read
      as a bare numeric table row (see _is_stripped_row).

    Guard: if the content already carries markdown table syntax (pipes),
    structure was preserved and neither signal fires.
    """
    lines = [ln for ln in content.splitlines() if ln.strip()]
    if not lines:
        return False

    # Converter already produced table syntax — structure is preserved
    if any(ln.strip().startswith("|") and "|" in ln[1:] for ln in lines):
        return False

    stripped_rows = sum(1 for ln in lines if _is_stripped_row(ln))
    if stripped_rows >= _ROW_MIN_COUNT:
        log.info("Flattened table detected: %d delimiter-stripped rows (%d lines)",
                 stripped_rows, len(lines))
        return True

    if len(lines) < _FLAT_MIN_LINES:
        return False

    short = sum(1 for ln in lines if len(ln.split()) <= 3)
    sentences = sum(1 for ln in lines if len(ln.split()) >= 6)
    numeric = sum(1 for ln in lines if re.search(r"\d", ln))

    n = len(lines)
    short_ratio = short / n
    sentence_ratio = sentences / n
    numeric_ratio = numeric / n

    is_flattened = (
        short_ratio >= _FLAT_SHORT_RATIO
        and sentence_ratio <= _FLAT_SENTENCE_RATIO
        and numeric_ratio >= _FLAT_NUMERIC_RATIO
    )

    if is_flattened:
        log.info(
            "Flattened table detected: short=%.2f sentence=%.2f numeric=%.2f (%d lines)",
            short_ratio, sentence_ratio, numeric_ratio, n,
        )

    return is_flattened
