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
the round-trip md → Doc → md is preserved.

Failure honesty: definitions were stripped pre-import, so any label whose
anchor cannot be processed gets its definition APPENDED BACK to the doc as
literal text (content is never silently lost), and the error is named in
cues. The fetch-side warn tier lives with the callers.
"""

from __future__ import annotations

import logging
import re

from adapters.http_client import get_sync_client
from tools.doc_chips import find_placeholder_indices

logger = logging.getLogger(__name__)

_DOCS_API = "https://docs.googleapis.com/v1/documents"

# `[^label]: definition` on its own line. Single-line definitions only —
# an indented continuation line stays in the body as literal text (v1).
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:[ \t]+(\S.*)$", re.MULTILINE)

# `[^label]` anchor anywhere (the def shape is excluded by callers using
# parse order: definitions are stripped first, then anchors counted).
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\s]+)\]")


def parse_footnotes(content: str) -> tuple[str, dict[str, str], list[str]]:
    """
    Split markdown footnote definitions out of content bound for Doc import.

    Returns (content_without_definitions, {label: definition}, warnings).
    Only labels with BOTH an anchor and a definition are extracted; a
    definition whose anchor is missing is left in place as literal text,
    an anchor whose definition is missing is left for the caller's warn
    tier — both named in warnings. A label whose anchor appears more than
    once is left literal too (Docs gives each reference its own footnote;
    de-duplicating would silently change meaning).
    """
    defs = {m.group(1): m.group(2) for m in FOOTNOTE_DEF_RE.finditer(content)}
    if not defs:
        return content, {}, []

    body_without_defs = FOOTNOTE_DEF_RE.sub("", content)
    anchor_counts: dict[str, int] = {}
    for m in FOOTNOTE_REF_RE.finditer(body_without_defs):
        anchor_counts[m.group(1)] = anchor_counts.get(m.group(1), 0) + 1

    warnings: list[str] = []
    extracted: dict[str, str] = {}
    kept_def_labels: list[str] = []
    for label, definition in defs.items():
        count = anchor_counts.get(label, 0)
        if count == 1:
            extracted[label] = definition
        elif count == 0:
            kept_def_labels.append(label)
            warnings.append(
                f"footnote definition [^{label}] has no matching anchor — "
                "left in the document as literal text"
            )
        else:
            kept_def_labels.append(label)
            warnings.append(
                f"footnote anchor [^{label}] appears {count} times — Docs "
                "footnotes are one-per-reference, so it was left as literal "
                "text with its definition"
            )

    orphan_anchors = sorted(set(anchor_counts) - set(defs))
    if orphan_anchors:
        warnings.append(
            "footnote anchor(s) with no definition left as literal text: "
            + ", ".join(f"[^{label}]" for label in orphan_anchors)
        )

    if not extracted:
        return content, {}, warnings

    # Strip ONLY the definitions being converted; kept ones stay in place.
    def _strip(m: re.Match[str]) -> str:
        return "" if m.group(1) in extracted else m.group(0)

    stripped = FOOTNOTE_DEF_RE.sub(_strip, content)
    # Collapse runs of blank lines the stripped definitions leave behind.
    stripped = re.sub(r"\n{3,}", "\n\n", stripped)
    return stripped, extracted, warnings


FootnoteState = tuple[dict[str, str], list[str]]  # (definitions, parse warnings)


def footnotes_for_import(doc_type: str | None, content: str | None) -> tuple[str | None, FootnoteState]:
    """One-line call-site wrapper: strip definitions when this is doc-bound
    markdown carrying any, else pass through untouched."""
    if doc_type == "doc" and content and FOOTNOTE_DEF_RE.search(content):
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
    replies). Any label whose anchor is not found post-import gets its
    definition appended back as literal text so no content is lost.
    Returns {"footnotes_inserted": N} and/or {"footnote_errors": [...]}.
    """
    client = get_sync_client()
    result: dict[str, object] = {}
    errors: list[str] = []

    anchors = {f"[^{label}]": label for label in definitions}
    found = find_placeholder_indices(doc_id, list(anchors))

    missing = [anchors[ph] for ph in anchors if ph not in found]
    ordered = sorted(
        ((found[ph][0], found[ph][1], anchors[ph]) for ph in found),
        key=lambda t: -t[0],
    )

    inserted = 0
    if ordered:
        try:
            requests: list[dict[str, object]] = []
            labels_in_order: list[str] = []
            for start, end, label in ordered:
                requests.append({"createFootnote": {"location": {"index": end}}})
                requests.append(
                    {"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}}
                )
                labels_in_order.append(label)
            resp = client.post_json(
                f"{_DOCS_API}/{doc_id}:batchUpdate", json_body={"requests": requests}
            )
            footnote_ids = [
                r["createFootnote"]["footnoteId"]
                for r in resp.get("replies", [])
                if "createFootnote" in r
            ]
            fills = [
                {
                    "insertText": {
                        "location": {"segmentId": fid, "index": 1},
                        "text": definitions[label],
                    }
                }
                for label, fid in zip(labels_in_order, footnote_ids)
            ]
            if fills:
                client.post_json(
                    f"{_DOCS_API}/{doc_id}:batchUpdate", json_body={"requests": fills}
                )
            inserted = len(footnote_ids)
        except Exception as e:  # noqa: BLE001 — post-import pass must not fail the create
            logger.warning("footnote pass failed for %s: %s", doc_id, e)
            errors.append(f"footnote pass failed ({e}) — anchors remain literal text")
            missing = sorted(definitions)  # restore every definition below
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
            if not errors:
                errors.append(
                    "anchor(s) not found after import for: "
                    + ", ".join(f"[^{label}]" for label in sorted(missing))
                    + " — their definitions were appended back as literal text"
                )
            else:
                errors.append("stripped definitions appended back as literal text — nothing lost")
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
