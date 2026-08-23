"""
Smart chips in Google Docs — whole-line @url → a real richLink (mise-rafote).

The explicit opt-in mirrors the Sheets whole-cell grain (mise-bazuvo): a chip
replaces the line with the target's LIVE title, server-enriched, which is why
it is opt-in and a bare URL stays a URL. Contract bought by probe (2026-08-09):
insertRichLink takes richLinkProperties.uri ONLY — the API rejects a supplied
title ("Insert rich link requests should not specify a title") and rejects
non-Workspace URLs ("The URL is invalid"); title and mimeType are enriched
server-side from the live resource, the Docs-side twin of Sheets chipRuns.

Split from tools/create.py at birth — that module is frozen by the size
ratchet (test_architecture.py), and new logic belongs in a fresh sibling.
find_placeholder_indices lives here too (moved from create.py) so the import
direction stays create → doc_chips with no cycle; the image-embedding pass in
create.py shares it.
"""

import re
from dataclasses import dataclass
from typing import Any

from adapters.http_client import get_sync_client
from logging_config import logger
from markdown_import import _FENCE_OPEN_RE
from validation import _WORKSPACE_FILE_DOMAINS

_DOCS_API = "https://docs.googleapis.com/v1/documents"

# Whole-line @url — trailing spaces tolerated, anything mid-prose is not a chip.
CHIP_REF_RE = re.compile(r"^@(https://\S+)[ \t]*$", re.MULTILINE)

_CHIP_PLACEHOLDER_PREFIX = "〔MISE_CHIP_"
_CHIP_PLACEHOLDER_SUFFIX = "〕"


@dataclass
class ChipRef:
    """A whole-line @url smart-chip request parsed from markdown."""
    index: int
    url: str
    placeholder: str


def parse_chip_refs(content: str) -> tuple[str, list[ChipRef]]:
    """Parse whole-line @url chip requests, replace with unique placeholders.

    Only Workspace-domain URLs become chips — insertRichLink rejects anything
    else outright, and batchUpdate is atomic, so gating here keeps one bad URL
    from failing every chip. A non-Workspace @url line keeps its literal text.

    Fenced code blocks are skipped: an @url line inside a fence is literal
    content the author marked as code (e.g. documenting this very syntax),
    and transforming it would be accept-and-transform. Fence detection is
    convert_fenced_blocks' own regex and close rule (CommonMark: an unclosed
    fence runs to end of input), so the two passes cannot disagree about
    what is code.
    """
    refs: list[ChipRef] = []
    out_lines: list[str] = []
    in_block = False
    close_re: re.Pattern[str] | None = None

    for line in content.split("\n"):
        if in_block:
            out_lines.append(line)
            if close_re and close_re.match(line):
                in_block = False
            continue
        fence = _FENCE_OPEN_RE.match(line)
        if fence:
            marker = fence.group(2)
            close_re = re.compile(
                rf"^\s*{re.escape(marker[0])}{{{len(marker)},}}\s*$"
            )
            in_block = True
            out_lines.append(line)
            continue
        chip = CHIP_REF_RE.match(line)
        if chip and any(d in chip.group(1) for d in _WORKSPACE_FILE_DOMAINS):
            idx = len(refs)
            placeholder = f"{_CHIP_PLACEHOLDER_PREFIX}{idx}{_CHIP_PLACEHOLDER_SUFFIX}"
            refs.append(ChipRef(index=idx, url=chip.group(1), placeholder=placeholder))
            out_lines.append(placeholder)
        else:
            out_lines.append(line)

    return "\n".join(out_lines), refs


def find_placeholder_indices(
    doc_id: str, placeholders: list[str],
) -> dict[str, tuple[int, int]]:
    """Find start/end indices of placeholder text in a Google Doc.

    Returns {placeholder: (startIndex, endIndex)} for each found placeholder.

    Concatenates all text runs in each paragraph before searching, so
    placeholders that span text run boundaries are still found.
    """
    client = get_sync_client()
    doc = client.get_json(
        f"{_DOCS_API}/{doc_id}",
        params={"fields": "body(content(paragraph(elements(textRun(content),startIndex,endIndex))))"},
    )

    result: dict[str, tuple[int, int]] = {}
    placeholder_set = set(placeholders)

    for item in doc.get("body", {}).get("content", []):
        elements = item.get("paragraph", {}).get("elements", [])
        if not elements:
            continue

        # Concatenate all text runs in this paragraph with their absolute positions
        para_text = ""
        para_start = elements[0].get("startIndex", 0)
        for elem in elements:
            para_text += elem.get("textRun", {}).get("content", "")

        # Search for each placeholder in the concatenated paragraph text
        for ph in placeholder_set:
            offset = para_text.find(ph)
            if offset >= 0:
                start = para_start + offset
                end = start + len(ph)
                result[ph] = (start, end)

    return result


def insert_chips_in_doc(doc_id: str, refs: list[ChipRef]) -> dict[str, Any]:
    """Post-import: replace chip placeholders with real smart chips.

    One atomic batchUpdate, delete+insertRichLink per site in reverse index
    order (prevents drift, same discipline as create.py's image pass).

    batchUpdate is atomic, so one bad chip fails the whole pass — the fallback
    rewrites every placeholder back to its literal @url text, so a failed pass
    never leaves sentinel residue in the document.
    """
    if not refs:
        return {}

    errors: list[str] = []
    indices = find_placeholder_indices(doc_id, [r.placeholder for r in refs])

    sites: list[tuple[int, int, ChipRef]] = []
    for ref in refs:
        found = indices.get(ref.placeholder)
        if not found:
            errors.append(f"Chip placeholder not found in doc for {ref.url}")
            continue
        sites.append((found[0], found[1], ref))
    sites.sort(key=lambda s: s[0], reverse=True)

    batch: list[dict[str, Any]] = []
    for start, end, ref in sites:
        batch.append({
            "deleteContentRange": {
                "range": {"startIndex": start, "endIndex": end, "segmentId": ""},
            }
        })
        batch.append({
            "insertRichLink": {
                "location": {"index": start, "segmentId": ""},
                "richLinkProperties": {"uri": ref.url},
            }
        })

    inserted = 0
    if batch:
        try:
            client = get_sync_client()
            client.post_json(
                f"{_DOCS_API}/{doc_id}:batchUpdate",
                json_body={"requests": batch},
            )
            inserted = len(sites)
        except Exception as e:
            errors.append(f"Chip insertion failed: {e}")
            _restore_chip_placeholders(doc_id, refs)

    result: dict[str, Any] = {}
    if inserted:
        result["chips_inserted"] = inserted
    if errors:
        result["chip_errors"] = errors
    return result


def restore_placeholders(doc_id: str, replacements: list[tuple[str, str]]) -> None:
    """Failure cleanup: put literal text back where placeholders sit.

    Takes (placeholder, literal_text) pairs — shared by the chip pass
    (@url lines) and create.py's image pass (![alt](path) refs), so neither
    leaves sentinel residue in a document when its batch fails.
    replaceAllText is index-free so it cannot drift. Best-effort — if even
    this fails the placeholders stand, and the error cue already names the
    primary failure.
    """
    try:
        client = get_sync_client()
        client.post_json(
            f"{_DOCS_API}/{doc_id}:batchUpdate",
            json_body={"requests": [
                {"replaceAllText": {
                    "containsText": {"text": placeholder, "matchCase": True},
                    "replaceText": literal,
                }}
                for placeholder, literal in replacements
            ]},
        )
    except Exception as e:
        # A failed restore leaves sentinel placeholders in the user's doc —
        # the one outcome the atomic-chips contract promises can't happen.
        # Can't raise here (we're already on a failure path), so log loudly
        # instead of passing silently (mise-pagigo).
        logger.warning(f"chip placeholder restore failed, sentinel residue possible: {e!r}")


def _restore_chip_placeholders(doc_id: str, refs: list[ChipRef]) -> None:
    """Chip-flavoured wrapper: placeholders back to their literal @url lines."""
    restore_placeholders(doc_id, [(r.placeholder, f"@{r.url}") for r in refs])
