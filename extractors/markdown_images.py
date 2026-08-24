"""
Markdown image extraction — pure functions lifting embedded images out of
Drive's markdown export.

Drive's docx→Doc→markdown conversion embeds retained images as base64 data
URIs (reference-style `[imageN]: <data:image/png;base64,…>` definitions, or
occasionally inline `![alt](data:…)`), and leaves a dangling `![][imageN]`
reference when an image did not survive. Both shapes are bad for deposits:
the base64 bloats content.md (92.2% of one real deposit), poisons grep
counts (short strings match inside the payload), and is unreadable by the
Read tool; the dangling ref is a silent drop.

This module rewrites the markdown so every decodable image becomes a
sidecar figure file referenced by relative path, and names what it could
not decode. No I/O — callers write the returned bytes to disk.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field

# `[imageN]: <data:image/png;base64,AAAA…>` — Drive writes the whole
# definition on one line, with or without angle brackets.
_REF_DEF_PATTERN = re.compile(
    r"^\[(?P<ref>[^\]]+)\]:\s*<?"
    r"data:image/(?P<subtype>[A-Za-z0-9.+-]+);base64,(?P<payload>[A-Za-z0-9+/=\s]+?)"
    r">?\s*$",
    re.MULTILINE,
)

# `![alt](data:image/png;base64,AAAA…)` — inline form.
_INLINE_PATTERN = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\("
    r"<?data:image/(?P<subtype>[A-Za-z0-9.+-]+);base64,(?P<payload>[A-Za-z0-9+/=]+)>?"
    r"\)"
)

# `![alt][ref]` — reference-style image usage.
_REF_USE_PATTERN = re.compile(r"!\[[^\]]*\]\[(?P<ref>[^\]]+)\]")

# Any reference definition line (data URI or not) — for dangling detection.
_ANY_DEF_PATTERN = re.compile(r"^\[(?P<ref>[^\]]+)\]:", re.MULTILINE)

_SUBTYPE_EXT = {
    "png": "png",
    "jpeg": "jpg",
    "jpg": "jpg",
    "gif": "gif",
    "webp": "webp",
    "bmp": "bmp",
    "svg+xml": "svg",
    "tiff": "tif",
    "x-emf": "emf",
    "emf": "emf",
    "x-wmf": "wmf",
    "wmf": "wmf",
}


@dataclass
class MarkdownFigure:
    """One image lifted out of the markdown."""

    filename: str  # e.g. "figure-1.png"
    data: bytes
    source_ref: str | None = None  # "image1" for reference-style; None for inline


@dataclass
class MarkdownImageExtraction:
    """Result of lifting embedded images out of a markdown document."""

    markdown: str
    figures: list[MarkdownFigure] = field(default_factory=list)
    dangling_refs: list[str] = field(default_factory=list)  # used but undefined
    notes: list[str] = field(default_factory=list)  # e.g. undecodable payloads


def _ext_for(subtype: str) -> str:
    return _SUBTYPE_EXT.get(subtype.lower(), subtype.lower())


def extract_markdown_images(
    markdown: str, name_prefix: str = "figure"
) -> MarkdownImageExtraction:
    """
    Lift base64 data-URI images out of markdown into figure byte blobs.

    Reference-style definitions are rewritten in place to point at the
    figure filename (`[image1]: figure-1.png`), so existing `![][image1]`
    uses keep resolving. Inline data URIs become `![alt](figure-N.ext)`.
    Figures are numbered in order of appearance. An undecodable payload is
    left untouched and named in notes — never silently dropped.
    """
    figures: list[MarkdownFigure] = []
    notes: list[str] = []
    counter = 0

    def _decode(payload: str) -> bytes | None:
        try:
            return base64.b64decode(re.sub(r"\s+", "", payload), validate=True)
        except (binascii.Error, ValueError):
            return None

    def _replace_def(m: re.Match[str]) -> str:
        nonlocal counter
        data = _decode(m.group("payload"))
        if data is None:
            notes.append(
                f"definition [{m.group('ref')}] carries an undecodable base64 "
                "payload — left in place"
            )
            return m.group(0)
        counter += 1
        filename = f"{name_prefix}-{counter}.{_ext_for(m.group('subtype'))}"
        figures.append(
            MarkdownFigure(filename=filename, data=data, source_ref=m.group("ref"))
        )
        return f"[{m.group('ref')}]: {filename}"

    def _replace_inline(m: re.Match[str]) -> str:
        nonlocal counter
        data = _decode(m.group("payload"))
        if data is None:
            notes.append("an inline image carries an undecodable base64 payload — left in place")
            return m.group(0)
        counter += 1
        filename = f"{name_prefix}-{counter}.{_ext_for(m.group('subtype'))}"
        figures.append(MarkdownFigure(filename=filename, data=data, source_ref=None))
        return f"![{m.group('alt')}]({filename})"

    rewritten = _REF_DEF_PATTERN.sub(_replace_def, markdown)
    rewritten = _INLINE_PATTERN.sub(_replace_inline, rewritten)

    defined = {m.group("ref") for m in _ANY_DEF_PATTERN.finditer(rewritten)}
    used = [m.group("ref") for m in _REF_USE_PATTERN.finditer(rewritten)]
    seen: set[str] = set()
    dangling: list[str] = []
    for ref in used:
        if ref not in defined and ref not in seen:
            dangling.append(ref)
            seen.add(ref)

    return MarkdownImageExtraction(
        markdown=rewritten, figures=figures, dangling_refs=dangling, notes=notes
    )
