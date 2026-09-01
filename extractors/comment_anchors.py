"""
Comment-anchor locators — pure resolution of preview anchor maps (mise-dukacu).

Turns the raw Slides/Sheets anchored-comments payload (see
`adapters/comment_anchors.py`) into `comment_id → AnchorLocator`, so
`comments.md` can say *slide 3 (Roadmap)* or *Sheet1!B12* instead of listing
comments in whatever order the Drive API happened to return them.

**The join is deliberately one-directional and outer.** The Drive comments
plane decides WHICH comments exist and render; this map only ever *adds* a
locator to one of them. A comment the preview read never mentions — a thread
created between the two calls, a surface Google changes under us, a paginated
tail — keeps its place in comments.md with no locator. There is no code path
here that can remove a comment from the deposit, which is the failure Sameer
named: *"we miss some gnarly edge cases and lose comments to the void."*

Three states, all rendered distinguishably rather than silently alike:

- **located** — the anchorId resolves to a slide or a cell range.
- **orphaned** — the thread carries an anchorId that resolves to nothing in the
  document. That is what a comment on a deleted slide or cleared range looks
  like; the UI labels these "Original content deleted" (probe evidence 42).
- **no locator** — the thread has no anchorId at all (a document-level comment),
  or the payload carried no anchor map to judge it against. An absent map is a
  fact about the instrument, so it never mints an "orphaned" verdict.
"""

import re
from dataclasses import dataclass
from typing import Any, Sequence

# Sort keys for anything that has a locator but no position in THIS deposit.
# Order: located comments, then ones on content the deposit never saw, then
# orphans, then (in the renderer) comments with no locator at all.
_ABSENT_ORDER = (2**31 - 1,)
_ORPHAN_ORDER = (2**31,)

# A slide the anchor map knows and the deposit does not. Saying "slide 2" here
# would name a DIFFERENT slide from the deposit's slide 2, and a locator that
# lies is worse than one that is missing — the reader has no way to doubt it.
# Reachable in a real fetch: mise reads the deck, spends up to ~20s fetching
# thumbnails, then reads the anchors, and a slide inserted in that window is in
# the second read and not the first (essayeur, mise-dukacu).
_ABSENT_SLIDE = (
    "⚠ on a slide that is not in this deposit — the deck changed between "
    "reading it and reading its comments; re-fetch to place this one"
)

# A1 tab names only need no quoting when they are plain word characters.
_PLAIN_TAB = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class AnchorLocator:
    """Where one comment thread sits in a deck or a workbook."""

    order: tuple[int, ...]  # document-order sort key
    label: str  # rendered locator, e.g. "slide 3 (Roadmap)" or "Sheet1!B12"
    quote: str = ""  # plainTextQuote — the anchored text, may be empty
    orphaned: bool = False  # anchorId present, resolves to nothing


def column_label(index0: int) -> str:
    """0-based column index → spreadsheet letters (0=A, 25=Z, 26=AA, 702=AAA)."""
    if index0 < 0:
        return ""
    letters = ""
    n = index0
    while True:
        letters = chr(ord("A") + n % 26) + letters
        n = n // 26 - 1
        if n < 0:
            return letters


def _norm(text: str) -> str:
    """Collapse runs of whitespace — Docs splits a sentence across textRuns and
    ends paragraphs with a newline, so a caller's quote never matches raw."""
    return re.sub(r"\s+", " ", text).strip()


def column_index(letters: str) -> int:
    """Spreadsheet letters → 0-based column index. Inverse of column_label."""
    n = 0
    for ch in letters.upper():
        if not ("A" <= ch <= "Z"):
            raise ValueError(f"{letters!r} is not a column reference")
        n = n * 26 + (ord(ch) - ord("A") + 1)
    if n == 0:
        raise ValueError("empty column reference")
    return n - 1


def parse_a1_cell(spec: str) -> tuple[str | None, int, int]:
    """`'Sheet1!B12'` / `'B12'` → (tab name or None, row0, col0).

    A comment anchors to ONE cell (the API takes a GridCoordinate, not a range),
    so a range spelling is refused rather than quietly reduced to its corner —
    a caller who wrote `B2:D5` meant something this surface cannot do, and
    silently commenting on B2 would be a different act from the one they asked
    for. Tab names may be single-quoted, with `''` for a literal quote.
    """
    text = spec.strip()
    if not text:
        raise ValueError("empty cell reference")

    tab: str | None = None
    if text.startswith("'"):
        end = 1
        while end < len(text):
            if text[end] == "'":
                if end + 1 < len(text) and text[end + 1] == "'":
                    end += 2
                    continue
                break
            end += 1
        else:
            raise ValueError(f"unterminated quoted tab name in {spec!r}")
        tab = text[1:end].replace("''", "'")
        rest = text[end + 1:]
        if not rest.startswith("!"):
            raise ValueError(f"expected `!` after the tab name in {spec!r}")
        text = rest[1:]
    elif "!" in text:
        tab, text = text.split("!", 1)
        if not tab.strip():
            raise ValueError(f"{spec!r} has a `!` with no tab name before it")

    if ":" in text:
        raise ValueError(
            f"{spec!r} is a range; a comment anchors to a single cell (e.g. B12)"
        )
    m = re.fullmatch(r"\$?([A-Za-z]+)\$?([0-9]+)", text.strip())
    if not m:
        raise ValueError(f"{text.strip()!r} is not a cell reference like B12")
    row = int(m.group(2))
    if row < 1:
        raise ValueError("row numbers start at 1")
    return (tab, row - 1, column_index(m.group(1)))


def parse_slide_spec(spec: str) -> tuple[str, str | int]:
    """`'slide 3'` / `'3'` → ('index', 2); anything else → ('object_id', spec).

    The `slide N` spelling is deliberately the one `comments.md` PRINTS, so the
    read side and the write side share one vocabulary: a Claude that has just
    read `↳ slide 3 (Roadmap)` can write `anchor='slide 3'` without translating.
    """
    text = spec.strip()
    m = re.fullmatch(r"(?:slide\s+)?(\d+)", text, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        if n < 1:
            raise ValueError("slide numbers start at 1")
        return ("index", n - 1)
    if not text:
        raise ValueError("empty slide reference")
    return ("object_id", text)


def utf16_len(text: str) -> int:
    """Length in UTF-16 code units — the unit Docs API indices count in.

    Python offsets count code points, so one emoji before a match slides every
    later index by one and the comment anchors a character or two off target,
    under a 200. `tools/doc_chips.py` learned this on 2026-08-24 (mise-rubucu,
    also from an essayeur probe); this module met the identical bug on
    2026-09-01 by not borrowing the lesson from its sibling.
    """
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def _tab_bodies(payload: dict[str, Any]) -> list[tuple[str | None, dict[str, Any]]]:
    """[(tabId, body)] for a documents.get payload — every tab, at every depth.

    Tabs NEST (`Tab.childTabs`), so a document showing one root tab can hold
    several. Counting only the top level made a nested-tab document look
    single-tab to the multi-tab guard and let it through.
    """
    tabs = payload.get("tabs")
    if isinstance(tabs, list) and tabs:
        out: list[tuple[str | None, dict[str, Any]]] = []

        def walk(entries: Any) -> None:
            for t in entries or []:
                if not isinstance(t, dict):
                    continue
                body = (t.get("documentTab") or {}).get("body") or {}
                out.append(((t.get("tabProperties") or {}).get("tabId"), body))
                walk(t.get("childTabs"))

        walk(tabs)
        return out
    return [(None, payload.get("body") or {})]


def _text_runs(elements: Any) -> list[tuple[int, dict[str, Any]]]:
    """(startIndex, textRun) for every run under a list of StructuralElements.

    Recurses into tables, because a `StructuralElement` is a paragraph OR a
    table OR a table of contents, and text inside a table is text a caller can
    quote. Missing it produced a false uniqueness count — a phrase appearing
    once in prose and once in a table read as unique and anchored to the prose
    copy, with `plainTextQuote` echoing identical text so nothing downstream
    could notice. Headers, footers and footnotes are separate collections that
    `body.content` cannot reach at all; callers are told so rather than told
    the text does not exist.
    """
    runs: list[tuple[int, dict[str, Any]]] = []
    for element in elements or []:
        if not isinstance(element, dict):
            continue
        for run in ((element.get("paragraph") or {}).get("elements") or []):
            text_run = run.get("textRun")
            start = run.get("startIndex")
            if text_run and start is not None:
                runs.append((start, text_run))
        table = element.get("table")
        if isinstance(table, dict):
            for row in table.get("tableRows") or []:
                for cell in (row or {}).get("tableCells") or []:
                    runs.extend(_text_runs((cell or {}).get("content")))
        toc = element.get("tableOfContents")
        if isinstance(toc, dict):
            runs.extend(_text_runs(toc.get("content")))
    return runs


def locate_quote(payload: dict[str, Any], quote: str) -> list[tuple[int, int]]:
    """Every (startIndex, endIndex) in the document matching `quote`.

    Returns ALL occurrences, because the count is the caller's business: one is
    an anchor, several are an ambiguity that must be refused rather than guessed
    at, and none is a miss. Whitespace in the document is normalised for
    matching (Docs splits a sentence across runs and inserts newlines at
    paragraph ends) while the returned indices address the raw text.

    **The payload must come from a `suggestionsViewMode=SUGGESTIONS_INLINE`
    read.** Measured 2026-09-01: `insertComment.range` is interpreted in that
    index space, so a range resolved against `PREVIEW_WITHOUT_SUGGESTIONS`
    anchors the comment to whatever sits at those indices once suggested
    insertions are counted back in — a different sentence, with a 200 and no
    sign of trouble (`docs/research/2026-09-01-jupuja-anchored-write/`, 21–24).
    """
    needle = _norm(quote)
    if not needle:
        return []

    hits: list[tuple[int, int]] = []
    for _tab_id, body in _tab_bodies(payload):
        # (index, character, width) — the index advances in UTF-16 code units,
        # which is what the API's startIndex/endIndex count.
        chars: list[tuple[int, str, int]] = []
        for start, text_run in _text_runs(body.get("content")):
            offset = 0
            for ch in text_run.get("content") or "":
                width = 2 if ord(ch) > 0xFFFF else 1
                chars.append((start + offset, ch, width))
                offset += width

        # Normalised haystack, keeping each kept character's real index and width.
        flat: list[str] = []
        index_of: list[int] = []
        width_of: list[int] = []
        previous_space = False
        for idx, ch, width in chars:
            if ch.isspace():
                if previous_space or not flat:
                    continue
                flat.append(" ")
                index_of.append(idx)
                width_of.append(width)
                previous_space = True
            else:
                flat.append(ch)
                index_of.append(idx)
                width_of.append(width)
                previous_space = False
        haystack = "".join(flat)

        at = haystack.find(needle)
        while at != -1:
            last = at + len(needle) - 1
            hits.append((index_of[at], index_of[last] + width_of[last]))
            at = haystack.find(needle, at + 1)
    return hits


def suggested_spans(payload: dict[str, Any]) -> list[tuple[int, int]]:
    """(start, end) of every run that exists only as a pending suggestion.

    Anchoring to suggested text is legal and lands, but the anchor is
    provisional: reject the suggestion and the thread orphans to "Original
    content deleted" — the very failure anchoring exists to avoid. The caller
    is cued rather than refused, because commenting ON a proposal is a
    reasonable thing to want to do.
    """
    spans: list[tuple[int, int]] = []
    for _tab_id, body in _tab_bodies(payload):
        for start, text_run in _text_runs(body.get("content")):
            if text_run.get("suggestedInsertionIds"):
                spans.append((start, start + utf16_len(text_run.get("content") or "")))
    return spans


def quote_tab(title: str) -> str:
    """Quote a tab name for A1 notation the way Sheets does."""
    if title and _PLAIN_TAB.match(title):
        return title
    return "'" + title.replace("'", "''") + "'"


def grid_range_to_a1(rng: dict[str, Any]) -> str:
    """Render a GridRange as an A1 range, or "" for a whole-sheet range.

    **Every field of a GridRange is optional** and the omissions are meaningful:
    no row bounds means the whole column, no column bounds means the whole row,
    none at all means the whole sheet. A renderer that assumes four integers
    produces a confidently wrong cell reference, so each bound is handled as
    present-or-open.
    """
    r0, r1 = rng.get("startRowIndex"), rng.get("endRowIndex")
    c0, c1 = rng.get("startColumnIndex"), rng.get("endColumnIndex")
    if r0 is None and r1 is None and c0 is None and c1 is None:
        return ""

    # Single cell: one row, one column, both bounded.
    if (
        r0 is not None and r1 is not None and c0 is not None and c1 is not None
        and r1 - r0 == 1 and c1 - c0 == 1
    ):
        return f"{column_label(c0)}{r0 + 1}"

    start = f"{column_label(c0) if c0 is not None else ''}{r0 + 1 if r0 is not None else ''}"
    end_col = column_label(c1 - 1) if c1 is not None else ""
    end_row = str(r1) if r1 is not None else ""  # r1 is exclusive, so r1-1+1 == r1
    end = f"{end_col}{end_row}"
    return f"{start}:{end}"


def _comment_anchor_ids(payload: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(comment_id, anchor_id, plain_text_quote) for every thread in the payload.

    A thread with no commentId is unjoinable and dropped; a thread with no
    anchorId is kept with an empty anchor so the caller can tell "unanchored"
    from "absent from this payload".
    """
    out: list[tuple[str, str, str]] = []
    for c in payload.get("comments") or []:
        if not isinstance(c, dict):
            continue
        cid = c.get("commentId") or ""
        if not cid:
            continue
        out.append((cid, c.get("anchorId") or "", c.get("plainTextQuote") or ""))
    return out


def slides_locators(
    payload: dict[str, Any],
    deck: Sequence[tuple[str, str | None]] = (),
) -> dict[str, AnchorLocator]:
    """Resolve a Slides preview payload to per-slide locators.

    Args:
        payload: the raw preview read (`slides[].commentAnchors`, `comments[]`).
        deck: (slide_object_id, title) in deck order, from mise's own
            PresentationData — the deposit's slide numbering and titles, so a
            locator says the same "slide 3" the manifest's slides_index does.
            Empty falls back to the payload's own slide order, untitled.

    Both anchor shapes resolve through the SAME containment: a page anchor lists
    the slide itself, a UI-authored text anchor lists the shape inside it
    (objectId i1 + shapeTextAnchors), but either way the anchorId appears under
    the slide that holds it. Joining on the container rather than on the anchored
    object means no new anchor shape Google adds can break the slide number.
    """
    pages = [p for p in (payload.get("slides") or []) if isinstance(p, dict)]
    positions: dict[str, tuple[int, str | None]] = {}
    for i, (slide_id, title) in enumerate(deck):
        positions[slide_id] = (i, title)

    anchor_to_slide: dict[str, tuple[int, str | None] | None] = {}
    for fallback_index, page in enumerate(pages):
        object_id = page.get("objectId") or ""
        if positions:
            # Trust the deposit's numbering, never the payload's, when we have
            # one — None marks a slide the deposit does not contain.
            where = positions.get(object_id)
        else:
            where = (fallback_index, None)
        for anchor in page.get("commentAnchors") or []:
            if not isinstance(anchor, dict):
                continue
            anchor_id = anchor.get("anchorId")
            if anchor_id and anchor_id not in anchor_to_slide:
                anchor_to_slide[anchor_id] = where

    have_map = bool(pages)
    locators: dict[str, AnchorLocator] = {}
    for cid, anchor_id, quote in _comment_anchor_ids(payload):
        if not anchor_id:
            continue  # unanchored: no locator, still renders
        if anchor_id not in anchor_to_slide:
            if not have_map:
                continue  # no map read — say nothing rather than cry orphan
            locators[cid] = AnchorLocator(
                order=_ORPHAN_ORDER, label="", quote=quote, orphaned=True
            )
            continue
        found = anchor_to_slide[anchor_id]
        if found is None:
            # The anchor resolves — to a slide this deposit does not hold.
            locators[cid] = AnchorLocator(
                order=_ABSENT_ORDER, label=_ABSENT_SLIDE, quote=quote
            )
            continue
        index, title = found
        label = f"slide {index + 1}" + (f" ({title})" if title else "")
        locators[cid] = AnchorLocator(order=(index,), label=label, quote=quote)
    return locators


def sheets_locators(payload: dict[str, Any]) -> dict[str, AnchorLocator]:
    """Resolve a Sheets preview payload to per-cell locators (`Tab!B12`).

    The tab title comes from this same read rather than from the deposit's tab
    list, because a comment can sit on a tab a scoped `tabs=` fetch never
    deposited — dropping its locator would hide exactly the comment nobody is
    looking at.
    """
    tabs = [s for s in (payload.get("sheets") or []) if isinstance(s, dict)]
    anchor_to_cell: dict[str, tuple[tuple[int, int, int], str]] = {}
    for position, tab in enumerate(tabs):
        props = tab.get("properties") or {}
        title = props.get("title") or (
            f"sheetId {props['sheetId']}" if props.get("sheetId") is not None else "sheet"
        )
        for anchor in tab.get("commentAnchors") or []:
            if not isinstance(anchor, dict):
                continue
            anchor_id = anchor.get("anchorId")
            if not anchor_id or anchor_id in anchor_to_cell:
                continue
            rng = anchor.get("range") or {}
            a1 = grid_range_to_a1(rng) if isinstance(rng, dict) else ""
            label = f"{quote_tab(title)}!{a1}" if a1 else quote_tab(title)
            order = (
                position,
                rng.get("startRowIndex", -1) if isinstance(rng, dict) else -1,
                rng.get("startColumnIndex", -1) if isinstance(rng, dict) else -1,
            )
            anchor_to_cell[anchor_id] = (order, label)

    have_map = bool(tabs)
    locators: dict[str, AnchorLocator] = {}
    for cid, anchor_id, quote in _comment_anchor_ids(payload):
        if not anchor_id:
            continue
        found = anchor_to_cell.get(anchor_id)
        if found is None:
            if not have_map:
                continue
            locators[cid] = AnchorLocator(
                order=_ORPHAN_ORDER, label="", quote=quote, orphaned=True
            )
            continue
        order, label = found
        locators[cid] = AnchorLocator(order=order, label=label, quote=quote)
    return locators
