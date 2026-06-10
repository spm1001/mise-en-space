"""
DOCX markup counter — pure functions detecting flattened Word markup.

No I/O, no API calls. Takes raw XML bytes from a .docx archive and counts
the markup that Drive's markdown export silently flattens: tracked changes
(a tracked-DELETED clause reads as ordinary present text), comments, and
inline images. The counts feed cue warnings so the reader knows to go to
the source document rather than trusting a clean-looking extraction.

Deliberately regex-on-bytes, not an XML parse: .docx arrives from email
attachments (untrusted input), defusedxml is not a dependency, and counting
element occurrences doesn't need a tree. Regex can't be entity-bombed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Element-start patterns. The [ >/] tail excludes prefix collisions:
# <w:ins matches <w:instrText without it; <w:del matches <w:delText.
_INS_PATTERN = re.compile(rb"<w:ins[ >/]")
_DEL_PATTERN = re.compile(rb"<w:del[ >/]")
_MOVE_PATTERN = re.compile(rb"<w:move(?:From|To)[ >/]")
_AUTHOR_PATTERN = re.compile(rb'w:author="([^"]*)"')
_COMMENT_PATTERN = re.compile(rb"<w:comment[ >/]")
_DRAWING_PATTERN = re.compile(rb"<w:drawing[ >/]")


@dataclass
class DocxMarkupCounts:
    """Markup present in a .docx that flattened extraction won't show."""

    insertions: int = 0
    deletions: int = 0
    moves: int = 0
    authors: list[str] = field(default_factory=list)  # distinct, sorted
    comments: int = 0
    inline_images: int = 0

    @property
    def tracked_changes(self) -> int:
        return self.insertions + self.deletions + self.moves

    @property
    def has_flattened_content(self) -> bool:
        return bool(self.tracked_changes or self.comments or self.inline_images)


def count_docx_markup(
    document_xml: bytes,
    comments_xml: bytes | None = None,
) -> DocxMarkupCounts:
    """
    Count flattened-markup occurrences in a .docx's XML members.

    Args:
        document_xml: Raw bytes of word/document.xml
        comments_xml: Raw bytes of word/comments.xml, if present

    Returns:
        DocxMarkupCounts with element counts and distinct revision authors
    """
    authors = {
        m.group(1).decode("utf-8", errors="replace")
        for m in _AUTHOR_PATTERN.finditer(document_xml)
    }
    return DocxMarkupCounts(
        insertions=len(_INS_PATTERN.findall(document_xml)),
        deletions=len(_DEL_PATTERN.findall(document_xml)),
        moves=len(_MOVE_PATTERN.findall(document_xml)),
        authors=sorted(a for a in authors if a),
        comments=len(_COMMENT_PATTERN.findall(comments_xml or b"")),
        inline_images=len(_DRAWING_PATTERN.findall(document_xml)),
    )


def format_markup_warnings(counts: DocxMarkupCounts) -> list[str]:
    """
    Render counts as cue warnings naming the trap and the remedy.

    Empty list when nothing was flattened — a clean docx adds no noise.
    """
    warnings: list[str] = []
    if counts.tracked_changes:
        parts = []
        if counts.insertions:
            parts.append(f"{counts.insertions} insertion(s)")
        if counts.deletions:
            parts.append(f"{counts.deletions} deletion(s)")
        if counts.moves:
            parts.append(f"{counts.moves} move(s)")
        by = f" by {', '.join(counts.authors)}" if counts.authors else ""
        warnings.append(
            f"Word doc has {counts.tracked_changes} tracked change(s) "
            f"({', '.join(parts)}){by} — FLATTENED here: deleted text reads "
            "as present, insertions are unmarked. Inspect the raw .docx for "
            "redlines before relying on this extraction."
        )
    if counts.comments:
        warnings.append(
            f"{counts.comments} Word comment(s) were not extracted — "
            "discussion context is missing from this content."
        )
    if counts.inline_images:
        warnings.append(
            f"{counts.inline_images} inline image(s) were dropped from the "
            "markdown — figures, diagrams, or image-based redlines are not shown."
        )
    return warnings
