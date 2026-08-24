"""Markdown footnotes → real Google Docs footnotes (mise-rubucu).

Drive's markdown import engine has no footnote concept: a `[^N]` anchor
survives as literal bracket text and a `[^N]: definition` line as literal
prose. This module gives do(create)/do(overwrite) on doc_type='doc' the
same treatment smart chips get (tools/doc_chips.py): definitions are
stripped from the markdown BEFORE import, the anchors ride the import as
literal text, and a post-import Docs API pass replaces each anchor with a
real footnote (superscript reference + footnote-pane text).

Mechanics, probed live 2026-08-24 on a scratch doc: one batchUpdate takes
createFootnote-at-anchor-end + deleteContentRange-of-anchor pairs in
DESCENDING document order (each pair only shifts indices above it); the
replies carry footnoteIds in request order; a second batchUpdate inserts
each definition at index 1 of its footnote segment. The read side already
renders Docs footnotes as `[^N]` + definitions (extractors/docs.py), so
the round-trip md → Doc → md is preserved (labels renumber — real Docs
footnotes are numbered, so `[^note]` returns as `[^1]`).

Essayeur-hardened the same day: code is masked before parsing (a fenced or
inline-code `[^1]` is content, not an anchor — the same guard chips have);
ranges come from find_placeholder_meta in UTF-16 units with first-wins +
occurrence counts (an ambiguous anchor is refused, never guessed); batch 1
carries writeControl.requiredRevisionId so a concurrent edit fails loudly
instead of shifting ranges; and the two batches fail with distinct,
truthful messages.

Failure honesty throughout: definitions were stripped pre-import, so any
label whose anchor cannot be processed gets its definition APPENDED BACK
to the doc as literal text (content is never silently lost), and the
error is named in cues. Known limit: anchors inside table cells are not
found (the locator walks top-level paragraphs) — they take the appended-
literal path with a cue.
"""

from __future__ import annotations

import logging
import re

from adapters.http_client import get_sync_client
from markdown_import import _FENCE_OPEN_RE
from tools.doc_chips import find_placeholder_meta

logger = logging.getLogger(__name__)

_DOCS_API = "https://docs.googleapis.com/v1/documents"

# `[^label]: definition` on its own line (GFM allows no space after the
# colon). Single-line definitions only — an indented continuation line
# stays in the body as literal text (v1).
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:[ \t]*(\S[^\r\n]*)\r?$", re.MULTILINE)

# `[^label]` anchor anywhere in body text.
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\s]+)\]")

_INLINE_CODE_RE = re.compile(r"(?<!`)`[^`\n]+`(?!`)")


def _mask_code(content: str) -> str:
    """Blank fenced blocks and inline code spans, preserving every offset.

    Replacement is same-length spaces, so regex spans on the masked text
    map 1:1 onto the original. Fence detection reuses convert_fenced_blocks'
    own open/close rules (CommonMark: an unclosed fence runs to the end),
    so this pass cannot disagree with the import rewrite about what is code.
    """
    out_lines: list[str] = []
    in_block = False
    close_re: re.Pattern[str] | None = None
    for line in content.split("\n"):
        if in_block:
            out_lines.append(" " * len(line))
            if close_re and close_re.match(line):
                in_block = False
            continue
        fence = _FENCE_OPEN_RE.match(line)
        if fence:
            marker = fence.group(2)
            close_re = re.compile(rf"^\s*{re.escape(marker[0])}{{{len(marker)},}}\s*$")
            in_block = True
            out_lines.append(" " * len(line))
            continue
        out_lines.append(_INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line))
    return "\n".join(out_lines)


def parse_footnotes(content: str) -> tuple[str, dict[str, str], list[str]]:
    """
    Split markdown footnote definitions out of content bound for Doc import.

    Returns (content_without_extracted_definitions, {label: definition},
    warnings). All matching runs on a code-masked copy: a `[^1]` inside a
    fence or inline code span is content, never an anchor or definition.
    Only labels with exactly one anchor and exactly one definition are
    extracted; everything else stays literal and is named in warnings —
    a duplicate definition, duplicate anchor, orphan definition or orphan
    anchor is a disclosed degrade, never a silent one.
    """
    masked = _mask_code(content)
    def_matches = list(FOOTNOTE_DEF_RE.finditer(masked))
    if not def_matches:
        return content, {}, _orphan_anchor_warnings(masked)

    def_labels: dict[str, int] = {}
    for m in def_matches:
        def_labels[m.group(1)] = def_labels.get(m.group(1), 0) + 1

    # Anchor counts, on the masked text with definition lines blanked too.
    masked_no_defs = FOOTNOTE_DEF_RE.sub(lambda m: " " * len(m.group(0)), masked)
    anchor_counts: dict[str, int] = {}
    for m in FOOTNOTE_REF_RE.finditer(masked_no_defs):
        anchor_counts[m.group(1)] = anchor_counts.get(m.group(1), 0) + 1

    warnings: list[str] = []
    extracted: dict[str, str] = {}
    for m in def_matches:
        label = m.group(1)
        if label in extracted:
            continue
        if def_labels[label] > 1:
            warnings.append(
                f"footnote [^{label}] is defined {def_labels[label]} times — "
                "all left in the document as literal text"
            )
            continue
        count = anchor_counts.get(label, 0)
        if count == 1:
            # Slice the ORIGINAL for the definition text (masking never
            # touches def lines outside code, but be explicit) and drop any
            # trailing CR a CRLF source leaves behind.
            extracted[label] = content[m.start(2) : m.end(2)].rstrip("\r")
        elif count == 0:
            warnings.append(
                f"footnote definition [^{label}] has no matching anchor — "
                "left in the document as literal text"
            )
        else:
            warnings.append(
                f"footnote anchor [^{label}] appears {count} times — Docs "
                "footnotes are one-per-reference, so it was left as literal "
                "text with its definition"
            )

    orphan_anchors = sorted(set(anchor_counts) - set(def_labels))
    if orphan_anchors:
        warnings.append(
            "footnote anchor(s) with no definition left as literal text: "
            + ", ".join(f"[^{label}]" for label in orphan_anchors)
        )

    if not extracted:
        return content, {}, warnings

    # Strip ONLY the definitions being converted, by masked-match spans
    # (positions map 1:1 onto the original); kept ones stay in place.
    pieces: list[str] = []
    cursor = 0
    for m in def_matches:
        if m.group(1) in extracted:
            pieces.append(content[cursor : m.start()])
            cursor = m.end()
    pieces.append(content[cursor:])
    stripped = "".join(pieces)
    # Collapse runs of blank lines the stripped definitions leave behind.
    stripped = re.sub(r"\n{3,}", "\n\n", stripped)
    return stripped, extracted, warnings


def _orphan_anchor_warnings(masked: str) -> list[str]:
    """Anchors present with no definitions at all — the warn tier still fires."""
    labels = sorted({m.group(1) for m in FOOTNOTE_REF_RE.finditer(masked)})
    if not labels:
        return []
    return [
        "footnote-style anchor(s) with no definitions — left as literal "
        "text: " + ", ".join(f"[^{label}]" for label in labels)
    ]


FootnoteState = tuple[dict[str, str], list[str]]  # (definitions, parse warnings)


def footnotes_for_import(doc_type: str | None, content: str | None) -> tuple[str | None, FootnoteState]:
    """One-line call-site wrapper: strip definitions when this is doc-bound
    markdown carrying footnote syntax, else pass through untouched."""
    if doc_type == "doc" and content and (
        FOOTNOTE_DEF_RE.search(content) or FOOTNOTE_REF_RE.search(content)
    ):
        body, defs, warnings = parse_footnotes(content)
        return body, (defs, warnings)
    return content, ({}, [])


def apply_footnote_cues(cues: dict[str, object], doc_id: str, state: FootnoteState) -> None:
    """One-line call-site wrapper: run the post-import pass and cue results."""
    defs, warnings = state
    if defs:
        cues.update(insert_footnotes_in_doc(doc_id, defs))
    if warnings:
        existing = cues.setdefault("footnote_errors", [])
        if isinstance(existing, list):
            existing.extend(warnings)


def insert_footnotes_in_doc(doc_id: str, definitions: dict[str, str]) -> dict[str, object]:
    """
    Replace literal `[^label]` anchors in a Doc with real footnotes.

    Two batchUpdates (the second needs footnoteIds from the first's
    replies). Any label whose anchor is not found post-import — or found
    more than once (ambiguous: refusing beats guessing) — gets its
    definition appended back as literal text so no content is lost.
    Returns {"footnotes_inserted": N} and/or {"footnote_errors": [...]}.
    """
    client = get_sync_client()
    result: dict[str, object] = {}
    errors: list[str] = []

    anchors = {f"[^{label}]": label for label in definitions}
    found, counts, revision_id = find_placeholder_meta(doc_id, list(anchors))

    missing: list[str] = []
    ordered: list[tuple[int, int, str]] = []
    for ph, label in anchors.items():
        n = counts.get(ph, 0)
        if n == 1:
            start, end = found[ph]
            ordered.append((start, end, label))
        else:
            missing.append(label)
            if n > 1:
                errors.append(
                    f"anchor [^{label}] appears {n} times in the imported doc "
                    "(possibly including code-styled text) — ambiguous, so it "
                    "was left literal and its definition appended back"
                )
    ordered.sort(key=lambda t: -t[0])

    inserted = 0
    created_ids: list[str] = []
    labels_in_order: list[str] = []
    if ordered:
        try:
            requests: list[dict[str, object]] = []
            for start, end, label in ordered:
                requests.append({"createFootnote": {"location": {"index": end}}})
                requests.append(
                    {"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}}
                )
                labels_in_order.append(label)
            body: dict[str, object] = {"requests": requests}
            if revision_id:
                # A concurrent edit between our read and this write would
                # shift every range — make that a loud refusal, not a
                # wrong-range delete (essayeur, 2026-08-24).
                body["writeControl"] = {"requiredRevisionId": revision_id}
            resp = client.post_json(f"{_DOCS_API}/{doc_id}:batchUpdate", json_body=body)
            created_ids = [
                r["createFootnote"]["footnoteId"]
                for r in resp.get("replies", [])
                if "createFootnote" in r
            ]
        except Exception as e:  # noqa: BLE001 — post-import pass must not fail the create
            logger.warning("footnote anchor pass failed for %s: %s", doc_id, e)
            errors.append(
                f"footnote pass failed before any anchor was touched ({e}) — "
                "anchors remain literal text"
            )
            missing = sorted(definitions)
            labels_in_order = []

    if labels_in_order:
        try:
            fills = [
                {
                    "insertText": {
                        "location": {"segmentId": fid, "index": 1},
                        "text": definitions[label],
                    }
                }
                for label, fid in zip(labels_in_order, created_ids)
            ]
            if fills:
                client.post_json(
                    f"{_DOCS_API}/{doc_id}:batchUpdate", json_body={"requests": fills}
                )
            inserted = len(created_ids)
        except Exception as e:  # noqa: BLE001
            logger.warning("footnote fill pass failed for %s: %s", doc_id, e)
            errors.append(
                f"{len(created_ids)} footnote reference(s) were created but "
                f"their definitions could NOT be inserted ({e}) — the "
                "footnotes exist EMPTY; definitions appended as literal text"
            )
            missing = sorted(set(missing) | set(labels_in_order))
            inserted = 0

    if missing:
        restored = "\n\n" + "\n".join(
            f"[^{label}]: {definitions[label]}" for label in sorted(missing)
        ) + "\n"
        try:
            client.post_json(
                f"{_DOCS_API}/{doc_id}:batchUpdate",
                json_body={
                    "requests": [
                        {"insertText": {"endOfSegmentLocation": {}, "text": restored}}
                    ]
                },
            )
            not_found = [
                label for label in missing
                if not any(f"[^{label}]" in e for e in errors)
            ]
            if not_found:
                errors.append(
                    "anchor(s) not found after import for: "
                    + ", ".join(f"[^{label}]" for label in sorted(not_found))
                    + " — their definitions were appended back as literal text"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("footnote definition restore failed for %s: %s", doc_id, e)
            errors.append(
                "definitions could NOT be restored for: "
                + ", ".join(f"[^{label}]" for label in sorted(missing))
                + f" ({e}) — re-run the edit or add them by hand"
            )

    if inserted:
        result["footnotes_inserted"] = inserted
    if errors:
        result["footnote_errors"] = errors
    return result
