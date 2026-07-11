"""
Comments Extractor — Pure functions for converting comments data to text.

Receives FileCommentsData dataclass, returns markdown output.
No API calls, no MCP awareness.

When the caller passes ``document_markdown`` (the fetched doc's content.md),
each comment is *located* in the document tree — nearest heading + sub-group,
and the comments are rendered in document order rather than API order. This is
the read-side of the triage channel (mise-newosi / zifoka): a margin comment
anchored on a heading is a decision about the whole section below it, and that
structure is invisible from the flat anchor alone. Without ``document_markdown``
(sheets, slides, or any caller that doesn't supply it) the output is unchanged.
"""

import html
import re
from dataclasses import dataclass

from models import FileCommentsData, CommentData


def _format_date(iso_date: str | None) -> str:
    """Format ISO datetime to readable date."""
    if not iso_date:
        return ""
    # Parse ISO format: 2026-01-15T10:30:00.000Z
    try:
        return iso_date[:10]  # Just the YYYY-MM-DD part
    except (IndexError, TypeError):
        return ""


def _format_author(name: str, email: str | None) -> str:
    """Format author name with optional email."""
    if email:
        return f"{name} <{email}>"
    return name


# =============================================================================
# Document-location correlation (mise-jimive)
# =============================================================================

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BOLD_LABEL_RE = re.compile(r"^\*\*(.+?)\*\*\s*$")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(?:\[[ xX]\]\s+)?(.*\S)\s*$")


def _norm(text: str) -> str:
    """Normalise for matching: unescape HTML entities, collapse whitespace."""
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


@dataclass
class _DocLine:
    """One structurally-classified line of the fetched document."""

    kind: str  # 'heading' | 'label' | 'item' | 'text'
    text_norm: str  # cleaned, normalised text (markdown decoration stripped)
    heading: str | None  # nearest enclosing heading text
    label: str | None  # nearest enclosing bold sub-group label


@dataclass
class _Location:
    """Where a comment's anchor lands in the document tree."""

    order: int  # index in document order (for sorting)
    kind: str  # what the anchor landed on
    heading: str | None
    label: str | None


def _parse_document(markdown: str) -> list[_DocLine]:
    """Parse content.md into an ordered list of classified lines.

    Tracks the running heading (headings replace shallower/equal levels) and
    the current bold sub-group label (reset by any new heading).
    """
    lines: list[_DocLine] = []
    heading_stack: list[tuple[int, str]] = []  # (level, text)
    current_label: str | None = None

    for raw in markdown.split("\n"):
        m = _HEADING_RE.match(raw)
        if m:
            level = len(m.group(1))
            text = _norm(m.group(2))
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, text))
            current_label = None  # a new heading opens a fresh sub-group scope
            lines.append(_DocLine("heading", text, text, None))
            continue

        nearest_heading = heading_stack[-1][1] if heading_stack else None

        m = _BOLD_LABEL_RE.match(raw)
        if m:
            text = _norm(m.group(1))
            current_label = text
            lines.append(_DocLine("label", text, nearest_heading, text))
            continue

        m = _LIST_ITEM_RE.match(raw)
        if m:
            text = _norm(m.group(1))
            lines.append(_DocLine("item", text, nearest_heading, current_label))
            continue

        text = _norm(raw)
        if text:
            lines.append(_DocLine("text", text, nearest_heading, current_label))

    return lines


def _locate(quoted_text: str, doc_lines: list[_DocLine]) -> _Location | None:
    """Find where a comment's anchor sits in the parsed document.

    Matches the anchor's first line against the document lines — exact match
    first, then substring containment (both directions, length-guarded to avoid
    matching stray fragments). Returns None when there's no usable anchor or no
    match is found.
    """
    if not quoted_text.strip():
        return None
    q_lines = [ln for ln in (l.strip() for l in html.unescape(quoted_text).split("\n")) if ln]
    if not q_lines:
        return None
    first = _norm(q_lines[0])
    if len(first) < 4:
        return None

    # Pass 1: exact
    for i, dl in enumerate(doc_lines):
        if dl.text_norm == first:
            return _Location(i, dl.kind, dl.heading, dl.label)
    # Pass 2: containment, guarded
    for i, dl in enumerate(doc_lines):
        if len(dl.text_norm) >= 8 and (first in dl.text_norm or dl.text_norm in first):
            return _Location(i, dl.kind, dl.heading, dl.label)
    return None


def _format_location(loc: _Location) -> str | None:
    """Render the one-line locator shown under a comment header.

    Container-anchored comments (a heading or a group label) are flagged with ⚠
    because they scope everything beneath them — the load-bearing triage signal.
    """
    if loc.kind == "heading":
        return "*↳ ⚠ anchored on a section heading — applies to the whole section below*"
    if loc.kind == "label":
        where = f" (in {loc.heading})" if loc.heading else ""
        return f"*↳ ⚠ anchored on a group heading{where} — applies to the items under it*"
    # item / text — show the breadcrumb it falls in
    crumbs = [c for c in (loc.heading, loc.label) if c]
    if not crumbs:
        return None
    return f"*↳ {' › '.join(crumbs)}*"


def extract_comments_content(
    data: FileCommentsData,
    max_length: int | None = None,
    document_markdown: str | None = None,
) -> str:
    """
    Convert file comments data to markdown text.

    Populates data.warnings with extraction issues encountered.

    Args:
        data: FileCommentsData with file info and comments
        max_length: Optional character limit. Truncates if exceeded.
        document_markdown: Optional fetched document content (content.md). When
            provided, comments are located in the document tree (nearest heading
            + sub-group) and rendered in document order. When None, output is the
            flat, API-order rendering unchanged.

    Returns:
        Formatted comments content. Format:

            ## Comments on "Document Title" (5 total)

            ### [Alice Smith <alice@example.com>] • 2026-01-15
            > Quoted text from document

            This is the comment content.

            **Replies:**
            - **[Bob Jones <bob@example.com>]** (2026-01-16): I agree with this.
            - **[Carol White <carol@example.com>]** (2026-01-16): Let me check.

            ---

            ### [Next Author <next@example.com>] • 2026-01-17
            ...
    """
    content_parts: list[str] = []
    total_length = 0

    # Header
    header = f'## Comments on "{data.file_name}" ({data.comment_count} total)\n\n'
    content_parts.append(header)
    total_length += len(header)

    # No comments case
    if not data.comments:
        content_parts.append("*No comments found.*\n")
        return "".join(content_parts)

    # Check if any comments have quoted text (anchor context)
    has_any_anchor = any(c.quoted_text for c in data.comments)
    if not has_any_anchor and data.comments:
        data.warnings.append(
            "Anchor context not available for this file type "
            "(comments show what was said, not what text was highlighted)"
        )

    # Locate each comment in the document and order by document position.
    # Without document_markdown this is a no-op: locations are all None and the
    # original API order is preserved.
    doc_lines = _parse_document(document_markdown) if document_markdown else []
    located: list[tuple[CommentData, _Location | None]] = [
        (c, _locate(c.quoted_text, doc_lines) if doc_lines else None)
        for c in data.comments
    ]
    if doc_lines:
        big = len(doc_lines) + 1
        # Stable sort: matched comments in document order, unmatched keep their
        # relative (API) order at the end. Decorate-sort-undecorate so the sort
        # key is plain ints (and mypy can narrow the Optional location).
        decorated: list[tuple[int, int, tuple[CommentData, _Location | None]]] = []
        for idx, pair in enumerate(located):
            loc = pair[1]
            decorated.append((loc.order if loc is not None else big, idx, pair))
        decorated.sort(key=lambda d: (d[0], d[1]))
        located = [pair for _, _, pair in decorated]
        if any(loc is None for _, loc in located):
            data.warnings.append(
                "Some comments could not be located in the document "
                "(anchor text not found) — listed last in API order"
            )

    # Process each comment
    truncated = False
    i = 0
    for i, (comment, location) in enumerate(located):
        # Separator (except for first)
        if i > 0:
            sep = "\n---\n\n"
            if max_length and (total_length + len(sep)) > max_length:
                truncated = True
                break
            content_parts.append(sep)
            total_length += len(sep)

        # Build comment block
        comment_block = _format_comment(comment, location)

        if max_length:
            remaining = max_length - total_length
            if len(comment_block) > remaining:
                if remaining > 100:
                    content_parts.append(comment_block[:remaining])
                content_parts.append(
                    f"\n\n[... TRUNCATED at {max_length:,} chars ...]"
                )
                data.warnings.append(f"Content truncated at {max_length:,} characters")
                truncated = True
                break

        content_parts.append(comment_block)
        total_length += len(comment_block)

    # Add truncation notice if we stopped early
    # Guard: only check `i` if we iterated over comments
    if data.comments and max_length and not truncated and i < len(located) - 1:
        content_parts.append(
            f"\n\n[... TRUNCATED: showing {i + 1} of {data.comment_count} comments ...]"
        )

    return "".join(content_parts).strip()


def _format_mentions(emails: list[str]) -> str:
    """Format a list of mentioned emails as a display string."""
    if not emails:
        return ""
    mentions = ", ".join(f"@{email}" for email in emails)
    return f"*Mentions: {mentions}*\n"


def _format_anchor(quoted_text: str) -> str:
    """Render the anchor blockquote.

    Single-line anchors keep the 200-char cap. Multi-line span anchors get a
    ``> `` prefix on *every* line (the old code only prefixed the first, so
    continuation lines silently fell out of the blockquote) and are capped by
    total length with a "(+N more lines)" note rather than a mid-line cut.
    HTML entities in the anchor are decoded (quotedFileContent.value is escaped).
    """
    anchor = html.unescape(quoted_text)
    anchor_lines = [ln.rstrip() for ln in anchor.split("\n") if ln.strip()]

    if len(anchor_lines) <= 1:
        single = anchor_lines[0] if anchor_lines else ""
        if len(single) > 200:
            single = single[:200] + "…"
        return f"\n> {single}\n"

    rendered: list[str] = []
    total = 0
    for j, ln in enumerate(anchor_lines):
        if total + len(ln) > 500 and rendered:
            remaining = len(anchor_lines) - j
            rendered.append(f"> … (+{remaining} more line{'s' if remaining != 1 else ''})")
            break
        rendered.append(f"> {ln}")
        total += len(ln)
    return "\n" + "\n".join(rendered) + "\n"


def _format_comment(comment: CommentData, location: "_Location | None" = None) -> str:
    """Format a single comment with optional replies and document location."""
    parts: list[str] = []

    # Author and date header, with the comment id as a trailing code-span so a
    # Claude can target it with do(operation="comment_reply", comment_id=...).
    author_str = _format_author(comment.author_name, comment.author_email)
    date_str = _format_date(comment.created_time)
    id_suffix = f" · `{comment.id}`" if comment.id else ""
    if date_str:
        parts.append(f"### [{author_str}] • {date_str}{id_suffix}\n")
    else:
        parts.append(f"### [{author_str}]{id_suffix}\n")

    # Document location (only when correlated against document_markdown)
    if location is not None:
        loc_line = _format_location(location)
        if loc_line:
            parts.append(f"{loc_line}\n")

    # Resolved indicator
    if comment.resolved:
        parts.append("*[RESOLVED]*\n")

    # Mentions (if any)
    if comment.mentioned_emails:
        parts.append(_format_mentions(comment.mentioned_emails))

    # Quoted text (anchor)
    if comment.quoted_text:
        parts.append(_format_anchor(comment.quoted_text))

    # Comment content
    if comment.content:
        parts.append(f"\n{comment.content}\n")

    # Replies
    non_empty_replies = [r for r in comment.replies if r.content.strip()]
    if non_empty_replies:
        parts.append("\n**Replies:**\n")
        for reply in non_empty_replies:
            reply_author = _format_author(reply.author_name, reply.author_email)
            reply_date = _format_date(reply.created_time)
            # Include mentions in reply if present
            mentions_suffix = ""
            if reply.mentioned_emails:
                mentions_suffix = f" *[@{', @'.join(reply.mentioned_emails)}]*"
            # Indent continuation lines to keep multi-line content in list item
            content = reply.content
            if "\n" in content:
                lines = content.split("\n")
                content = lines[0] + "\n" + "\n".join("  " + line for line in lines[1:])
            if reply_date:
                parts.append(f"- **[{reply_author}]** ({reply_date}): {content}{mentions_suffix}\n")
            else:
                parts.append(f"- **[{reply_author}]**: {content}{mentions_suffix}\n")

    return "".join(parts)
