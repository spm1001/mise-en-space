"""
Comments Extractor — Pure functions for converting comments data to text.

Receives FileCommentsData dataclass, returns markdown output.
No API calls, no MCP awareness.
"""

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


def extract_comments_content(
    data: FileCommentsData,
    max_length: int | None = None,
) -> str:
    """
    Convert file comments data to markdown text.

    Populates data.warnings with extraction issues encountered.

    Args:
        data: FileCommentsData with file info and comments
        max_length: Optional character limit. Truncates if exceeded.

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

    # Process each comment
    truncated = False
    for i, comment in enumerate(data.comments):
        # Separator (except for first)
        if i > 0:
            sep = "\n---\n\n"
            if max_length and (total_length + len(sep)) > max_length:
                truncated = True
                break
            content_parts.append(sep)
            total_length += len(sep)

        # Build comment block
        comment_block = _format_comment(comment)

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
    if max_length and not truncated and i < len(data.comments) - 1:
        content_parts.append(
            f"\n\n[... TRUNCATED: showing {i + 1} of {data.comment_count} comments ...]"
        )

    return "".join(content_parts).strip()


def _format_comment(comment: CommentData) -> str:
    """Format a single comment with optional replies."""
    parts: list[str] = []

    # Author and date header
    author_str = _format_author(comment.author_name, comment.author_email)
    date_str = _format_date(comment.created_time)
    if date_str:
        parts.append(f"### [{author_str}] • {date_str}\n")
    else:
        parts.append(f"### [{author_str}]\n")

    # Resolved indicator
    if comment.resolved:
        parts.append("*[RESOLVED]*\n")

    # Quoted text (anchor)
    if comment.quoted_text:
        parts.append(f"\n> {comment.quoted_text}\n")

    # Comment content
    if comment.content:
        parts.append(f"\n{comment.content}\n")

    # Replies
    if comment.replies:
        parts.append("\n**Replies:**\n")
        for reply in comment.replies:
            reply_author = _format_author(reply.author_name, reply.author_email)
            reply_date = _format_date(reply.created_time)
            if reply_date:
                parts.append(f"- **[{reply_author}]** ({reply_date}): {reply.content}\n")
            else:
                parts.append(f"- **[{reply_author}]**: {reply.content}\n")

    return "".join(parts)
