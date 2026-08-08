"""
URL decorations — parse, resolve, point (mise-dogape).

A pasted Google URL often names a location INSIDE the document: ?gid= a sheet
tab, ?tab= a Doc tab, #heading= a heading, #slide= a slide, ?disco= a comment
thread. mise used to regex out the file id one line into the fetch and never
parse the rest at all — the caller said "this heading, in this document" and
got forty pages with no signal the aim was ignored.

The design rule (Sameer's reframe, 2026-07-27): POINT, DON'T NARROW. Deposits
are not the context cost — the later Read is — so everything is deposited
exactly as for a bare fetch, and the returned cue points at the deposited
artefact that holds the named location ("content.md from line 340", the
per-tab CSV, the comments.md entry). A pointer that no longer resolves is
reported as STALE rather than ignored: a dangling pointer is information
(deleted tab, resolved comment) that mise otherwise cannot detect.

Resolution never pattern-matches an id's shape ("resolve, don't validate"):
object ids vary wildly within one product ('p', 'g3f5d00ed841_0_0',
'mig_slide_003', 't.0', 't.ems17pqdjs5b'), so every remainder is treated as an
opaque key looked up against data the fetch already downloaded — zero extra
API calls. The one spelling quirk: Slides URLs prefix the objectId with "id."
that the API does not have; the other four decorations are verbatim.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import orjson

from models import DocData, FetchResult, PresentationData

# The five decorations this module understands, plus `range` which rides
# alongside gid on Sheets URLs and needs no resolution (it is already A1 text).
_DECORATION_KEYS = ("gid", "tab", "heading", "slide", "disco", "range")

# A rendered markdown heading line: 1-6 hashes, a space, then the text (which
# is empty for Docs' common empty-text headings — they render as "## ").
_HEADING_LINE = re.compile(r"^(#{1,6}) (.*)$")

# The inter-tab separator extract_doc_content emits, alone on its line.
_TAB_SEPARATOR = "=" * 60


@dataclass
class UrlDecorations:
    """What a Drive URL's tail named, plus any parse-level warnings."""

    values: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.values or self.warnings)


def parse_drive_url_decorations(url: str) -> UrlDecorations:
    """
    Read gid/tab/heading/slide/disco/range from a Drive URL's query AND fragment.

    Sheets and Slides carry the same identifier in both places (?gid=N#gid=N);
    when the two disagree we say so rather than picking (the brief's rule) and
    resolve neither. Slides' "id." prefix is stripped; everything else is kept
    verbatim — the "h." in a headingId is part of the API value.
    """
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    fragment = parse_qs(parts.fragment)

    result = UrlDecorations()
    for key in _DECORATION_KEYS:
        q_vals = query.get(key)
        f_vals = fragment.get(key)
        q_val = q_vals[0] if q_vals else None
        f_val = f_vals[0] if f_vals else None
        if q_val is not None and f_val is not None and q_val != f_val:
            result.warnings.append(
                f"URL carries {key}= twice with different values "
                f"(?{key}={q_val} vs #{key}={f_val}) — not picking between "
                f"them; pass the URL with a single {key} to resolve it."
            )
            continue
        value = q_val if q_val is not None else f_val
        if value is None:
            continue
        if key == "slide" and value.startswith("id."):
            value = value[len("id."):]
        result.values[key] = value
    return result


# =============================================================================
# MANIFEST STRUCTURE BUILDERS — called at deposit time, for every fetch,
# so a decorated and a bare fetch produce identical deposits.
# =============================================================================


def build_doc_structure(doc_data: DocData, content: str) -> dict[str, Any]:
    """
    Record where each tab and heading landed in the rendered content.md.

    mise generates content.md, so it knows the offsets; recording them in the
    manifest is what makes a later "?tab=…#heading=…" cue actionable — the cue
    names a line and Read's offset/limit consumes it directly.

    Line numbers are 1-based. Alignment between the API's heading list and the
    rendered heading lines is guarded by count+level agreement per tab; on any
    mismatch the headings keep id/text/tab and simply omit "line" — never a
    wrong number (the checkbox-oracle discipline).
    """
    lines = content.split("\n")
    separator_lines = [i + 1 for i, ln in enumerate(lines) if ln == _TAB_SEPARATOR]

    tabs_out: list[dict[str, Any]] = []
    headings_out: list[dict[str, Any]] = []

    # Tab k starts on the line after separator k-1 (tab 0 starts at line 1).
    # If the separator count disagrees with the tab count (a document line of
    # sixty "=" would fake one), only tab 0's start is trustworthy.
    aligned = len(separator_lines) == len(doc_data.tabs) - 1
    for i, tab in enumerate(doc_data.tabs):
        entry: dict[str, Any] = {"id": tab.tab_id, "title": tab.title}
        if i == 0:
            entry["start_line"] = 1
        elif aligned:
            entry["start_line"] = separator_lines[i - 1] + 1
        tabs_out.append(entry)

    for i, tab in enumerate(doc_data.tabs):
        expected = _tab_headings(tab)
        span_start = tabs_out[i].get("start_line")
        if aligned or len(doc_data.tabs) == 1:
            span_end = (
                separator_lines[i] - 1 if i < len(separator_lines) else len(lines)
            )
        else:
            span_start = None
            span_end = None

        rendered: list[tuple[int, int]] = []  # (1-based line, level)
        if span_start is not None and span_end is not None:
            for n in range(span_start, span_end + 1):
                m = _HEADING_LINE.match(lines[n - 1])
                if m:
                    rendered.append((n, len(m.group(1))))
            # extract_doc_content injects "# {tab title}" when the tab's body
            # doesn't start with an H1 — one extra rendered line, level 1.
            if len(rendered) == len(expected) + 1 and rendered[0][1] == 1:
                rendered = rendered[1:]

        matched = len(rendered) == len(expected) and all(
            lvl == e["level"] for (_, lvl), e in zip(rendered, expected)
        )
        for j, e in enumerate(expected):
            entry = {
                "id": e["id"],
                "level": e["level"],
                "text": e["text"],
                "tab_id": tab.tab_id,
            }
            if matched:
                entry["line"] = rendered[j][0]
            headings_out.append(entry)

    return {"tabs": tabs_out, "headings": headings_out}


def _tab_headings(tab: Any) -> list[dict[str, Any]]:
    """Heading paragraphs (id, level, text) from a tab's raw body, in order.

    Walks top-level paragraphs only — headings inside tables render as table
    cells, not '#' lines, so including them would break line alignment.
    """
    out: list[dict[str, Any]] = []
    for el in tab.body.get("content", []):
        para = el.get("paragraph")
        if not para:
            continue
        style = para.get("paragraphStyle", {})
        named = style.get("namedStyleType", "")
        if not named.startswith("HEADING_"):
            continue
        try:
            level = int(named.rsplit("_", 1)[1])
        except ValueError:
            continue
        text = "".join(
            e.get("textRun", {}).get("content", "") for e in para.get("elements", [])
        ).strip()
        out.append({"id": style.get("headingId"), "level": level, "text": text})
    return out


def build_slides_index(presentation_data: PresentationData) -> list[dict[str, Any]]:
    """Slide objectId → position map for the manifest, in deck order."""
    return [
        {
            "id": s.slide_id,
            "title": s.title,
            "has_thumbnail": s.thumbnail_bytes is not None,
        }
        for s in presentation_data.slides
    ]


# =============================================================================
# RESOLUTION — post-fetch, against the deposit alone (manifest + comments.md).
# =============================================================================


def apply_url_decorations(result: FetchResult, decorations: UrlDecorations) -> None:
    """
    Resolve what the URL named against the deposit and point the cues at it.

    Mutates result.cues: a human-readable `pointer` sentence for what resolved,
    plus warnings for anything stale, conflicting or inapplicable. Never
    raises — a fetch must not fail because its pointer could not be resolved —
    but a failure is disclosed as a warning rather than swallowed (the
    accept-and-drop rule).
    """
    try:
        _apply(result, decorations)
    except Exception as e:  # noqa: BLE001 — enrichment must never kill the fetch
        result.cues.setdefault("warnings", []).append(
            f"URL named a location ({', '.join(sorted(decorations.values))}) "
            f"but resolving it against the deposit failed: {e}"
        )


def _apply(result: FetchResult, decorations: UrlDecorations) -> None:
    cues = result.cues
    warnings: list[str] = list(decorations.warnings)
    sentences: list[str] = []
    values = decorations.values

    manifest: dict[str, Any] = {}
    manifest_path = Path(result.path) / "manifest.json"
    if manifest_path.exists():
        manifest = orjson.loads(manifest_path.read_bytes())

    handled: set[str] = set()
    if result.type in ("sheet", "xlsx") and "gid" in values:
        sentences, warnings = _resolve_gid(values, manifest, sentences, warnings)
        handled.update({"gid", "range"} & values.keys())
    if result.type == "doc" and ("tab" in values or "heading" in values):
        sentences, warnings = _resolve_doc(values, manifest, sentences, warnings)
        handled.update({"tab", "heading"} & values.keys())
    if result.type == "slides" and "slide" in values:
        sentences, warnings = _resolve_slide(values, manifest, sentences, warnings)
        handled.add("slide")
    if "disco" in values:
        sentences, warnings = _resolve_disco(
            values["disco"], Path(result.path), sentences, warnings
        )
        handled.add("disco")

    leftover = sorted(set(values) - handled - {"range"})
    if leftover:
        warnings.append(
            f"URL carries {', '.join(f'{k}=' for k in leftover)} which does not "
            f"apply to a {result.type} fetch — named here so it isn't silently "
            f"dropped."
        )

    if sentences:
        cues["pointer"] = " ".join(sentences)
    if warnings:
        cues.setdefault("warnings", []).extend(warnings)


def _resolve_gid(
    values: dict[str, str],
    manifest: dict[str, Any],
    sentences: list[str],
    warnings: list[str],
) -> tuple[list[str], list[str]]:
    gid = values["gid"]
    cells = f", cells {values['range']}" if "range" in values else ""
    tabs = manifest.get("tabs") or []
    known_ids = [t.get("sheet_id") for t in tabs if t.get("sheet_id") is not None]
    if not known_ids:
        warnings.append(
            f"URL names a tab (gid={gid}) but this deposit has no tab-id map "
            f"(Office files don't carry Google sheet ids) — check tab names "
            f"by hand."
        )
        return sentences, warnings
    for tab in tabs:
        if str(tab.get("sheet_id")) == gid:
            sentences.append(
                f"URL points at tab '{tab['name']}' — {tab['filename']}{cells}."
            )
            return sentences, warnings
    names = ", ".join(f"'{t['name']}'" for t in tabs)
    stale = (
        f"URL names a tab (gid={gid}) that is not in this spreadsheet — the "
        f"pointer is stale (tab deleted, or the link belongs to another file). "
        f"Tabs here: {names}."
    )
    sentences.append(stale)
    warnings.append(stale)
    return sentences, warnings


def _resolve_doc(
    values: dict[str, str],
    manifest: dict[str, Any],
    sentences: list[str],
    warnings: list[str],
) -> tuple[list[str], list[str]]:
    structure = manifest.get("structure") or {}
    tabs = structure.get("tabs") or []
    headings = structure.get("headings") or []
    multi_tab = len(tabs) > 1

    tab_entry = None
    if "tab" in values:
        tab_entry = next((t for t in tabs if t.get("id") == values["tab"]), None)
        if tab_entry is None:
            stale = (
                f"URL names a tab ({values['tab']}) that is not in this document "
                f"— tab ids are immutable, so the pointer is stale (the tab was "
                f"likely deleted)."
            )
            sentences.append(stale)
            warnings.append(stale)

    if "heading" in values:
        h = next((e for e in headings if e.get("id") == values["heading"]), None)
        if h is None:
            gentle = (
                f"URL names a heading ({values['heading']}) not found in this "
                f"document — it may have been deleted, or the link may predate "
                f"edits (heading ids are only read-only, not guaranteed stable)."
            )
            sentences.append(gentle)
            warnings.append(gentle)
            return sentences, warnings
        label = f"heading '{h['text']}'" if h["text"] else "an unnamed heading"
        if not h["text"]:
            context = _nearest_named_heading(headings, h)
            if context:
                label += f" (below '{context}')"
        h_tab = next((t for t in tabs if t.get("id") == h.get("tab_id")), None)
        tab_part = (
            f" in tab '{h_tab['title']}'" if h_tab is not None and multi_tab else ""
        )
        where = (
            f"content.md from line {h['line']}"
            if "line" in h
            else "content.md (line unresolved — search the heading text)"
        )
        sentences.append(f"URL points at {label}{tab_part} — {where}.")
        if tab_entry is not None and h.get("tab_id") != tab_entry.get("id"):
            warnings.append(
                f"URL's tab= ({tab_entry['title']}) and heading= disagree — the "
                f"heading lives in a different tab; trusting the heading."
            )
        return sentences, warnings

    if tab_entry is not None:
        where = (
            f"content.md from line {tab_entry['start_line']}"
            if "start_line" in tab_entry
            else "content.md"
        )
        sentences.append(f"URL points at tab '{tab_entry['title']}' — {where}.")
    return sentences, warnings


def _nearest_named_heading(
    headings: list[dict[str, Any]], target: dict[str, Any]
) -> str | None:
    """Nearest preceding non-empty heading in the same tab, for context."""
    best: str | None = None
    for e in headings:
        if e is target:
            return best
        if e.get("tab_id") == target.get("tab_id") and e.get("text"):
            best = e["text"]
    return best


def _resolve_slide(
    values: dict[str, str],
    manifest: dict[str, Any],
    sentences: list[str],
    warnings: list[str],
) -> tuple[list[str], list[str]]:
    slide_id = values["slide"]
    index = manifest.get("slides_index") or []
    total = len(index)
    for i, s in enumerate(index):
        if s.get("id") == slide_id:
            title_part = f" ('{s['title']}')" if s.get("title") else ""
            thumb = (
                f"slide_{i + 1:02d}.png"
                if s.get("has_thumbnail")
                else "no thumbnail was deposited for it"
            )
            sentences.append(
                f"URL points at slide {i + 1} of {total}{title_part} — {thumb}."
            )
            return sentences, warnings
    stale = (
        f"URL names a slide ({slide_id}) that is not in this deck "
        f"({total} slides) — the pointer looks stale (slide deleted, or the "
        f"link belongs to another deck)."
    )
    sentences.append(stale)
    warnings.append(stale)
    return sentences, warnings


def _resolve_disco(
    comment_id: str,
    deposit: Path,
    sentences: list[str],
    warnings: list[str],
) -> tuple[list[str], list[str]]:
    comments_path = deposit / "comments.md"
    if comments_path.exists() and f"`{comment_id}`" in comments_path.read_text(
        encoding="utf-8"
    ):
        sentences.append(
            f"URL points at comment thread {comment_id} — see its entry in "
            f"comments.md; reply with do(comment_reply, "
            f"comment_id='{comment_id}')."
        )
        return sentences, warnings
    gentle = (
        f"URL names comment {comment_id}, which is not among this file's open "
        f"comments — it has likely been resolved (comments.md holds open "
        f"threads only)."
    )
    sentences.append(gentle)
    warnings.append(gentle)
    return sentences, warnings
