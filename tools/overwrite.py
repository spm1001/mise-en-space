"""
Overwrite operation — replace full content of a Google Doc from markdown.

Uses Docs API batchUpdate: delete all content → insertText → apply heading styles.
Preserves file ID, sharing, location, and revision history.
"""

import re
from typing import Any

from adapters.services import get_docs_service
from models import MiseError, ErrorKind
from retry import with_retry


# Markdown heading level → Docs named style
_HEADING_STYLES = {
    1: "HEADING_1",
    2: "HEADING_2",
    3: "HEADING_3",
    4: "HEADING_4",
    5: "HEADING_5",
    6: "HEADING_6",
}

# Regex: lines starting with 1-6 '#' characters followed by space
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def do_overwrite(
    file_id: str,
    content: str | None = None,
    source: "Path | None" = None,
) -> dict[str, Any]:
    """
    Replace full content of a Google Doc with new markdown.

    Args:
        file_id: Target document ID
        content: Markdown content (mutually exclusive with source)
        source: Path to deposit folder with content.md

    Returns:
        Dict with file_id, title, web_link, operation, cues
    """
    # Resolve content
    if source and content:
        return {
            "error": True,
            "kind": "invalid_input",
            "message": "Provide 'content' or 'source', not both",
        }

    if source:
        from pathlib import Path

        source_path = Path(source)
        content_file = source_path / "content.md"
        if not content_file.exists():
            return {
                "error": True,
                "kind": "invalid_input",
                "message": f"No content.md in source folder: {source_path}",
            }
        content = content_file.read_text(encoding="utf-8")

    if not content:
        return {
            "error": True,
            "kind": "invalid_input",
            "message": "overwrite requires 'content' or 'source'",
        }

    try:
        return _overwrite_doc(file_id, content)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


@with_retry(max_attempts=3, delay_ms=1000)
def _overwrite_doc(file_id: str, markdown: str) -> dict[str, Any]:
    """Replace document content via Docs API batchUpdate."""
    service = get_docs_service()

    # Get current document to find end index and title
    doc = (
        service.documents()
        .get(documentId=file_id, fields="title,body(content(endIndex))")
        .execute()
    )

    title = doc.get("title", "Untitled")

    # Find end index of document body
    body_content = doc.get("body", {}).get("content", [])
    if not body_content:
        end_index = 1
    else:
        end_index = body_content[-1].get("endIndex", 1)

    # Strip markdown headings to plain text for insertion, track positions
    plain_text, heading_map = _strip_headings(markdown)

    # Build batchUpdate requests
    requests: list[dict[str, Any]] = []

    # 1. Delete existing content (if any beyond the implicit newline)
    if end_index > 1:
        requests.append({
            "deleteContentRange": {
                "range": {
                    "startIndex": 1,
                    "endIndex": end_index - 1,
                }
            }
        })

    # 2. Insert new text at index 1
    if plain_text:
        requests.append({
            "insertText": {
                "location": {"index": 1},
                "text": plain_text,
            }
        })

    # 3. Apply heading styles (after insert, indices are relative to new content)
    for start_idx, end_idx, level in heading_map:
        style_name = _HEADING_STYLES.get(level)
        if style_name:
            requests.append({
                "updateParagraphStyle": {
                    "range": {
                        "startIndex": start_idx + 1,  # +1 for doc's 1-based body start
                        "endIndex": end_idx + 1,
                    },
                    "paragraphStyle": {"namedStyleType": style_name},
                    "fields": "namedStyleType",
                }
            })

    if requests:
        service.documents().batchUpdate(
            documentId=file_id,
            body={"requests": requests},
        ).execute()

    return {
        "file_id": file_id,
        "title": title,
        "web_link": f"https://docs.google.com/document/d/{file_id}/edit",
        "operation": "overwrite",
        "cues": {
            "char_count": len(plain_text),
            "heading_count": len(heading_map),
        },
    }


def _strip_headings(markdown: str) -> tuple[str, list[tuple[int, int, int]]]:
    """
    Convert markdown headings to plain text and record their positions.

    Returns:
        (plain_text, heading_map) where heading_map is list of
        (start_index, end_index, level) tuples relative to the plain text.
    """
    # Process line by line to track positions accurately
    lines = markdown.split("\n")
    output_lines: list[str] = []
    heading_map: list[tuple[int, int, int]] = []

    current_pos = 0
    for line in lines:
        match = _HEADING_RE.match(line)
        if match:
            hashes, text = match.groups()
            level = len(hashes)
            # Record position of heading text in output
            heading_map.append((current_pos, current_pos + len(text), level))
            output_lines.append(text)
            current_pos += len(text) + 1  # +1 for newline
        else:
            output_lines.append(line)
            current_pos += len(line) + 1

    plain_text = "\n".join(output_lines)
    return plain_text, heading_map
