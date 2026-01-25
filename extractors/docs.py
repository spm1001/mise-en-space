"""
Docs Extractor — Pure function for converting Google Docs to markdown.

Receives DocData with tabs, returns combined markdown output.
No API calls, no MCP awareness.
"""

from typing import Any

from models import DocData


def extract_doc_content(
    data: DocData,
    max_length: int | None = None,
) -> str:
    """
    Convert document data to markdown with tab headers.

    Populates data.warnings with extraction issues encountered.

    Args:
        data: DocData with title and tabs
        max_length: Optional character limit. Truncates if exceeded.

    Returns:
        Markdown text with tab headers like:
            # Tab Title

            Content here...

            ============================================================
            # Second Tab

            More content...
    """
    content_parts: list[str] = []
    total_length = 0

    # Clear any existing warnings and set up tracking
    data.warnings = []
    unknown_elements: set[str] = set()
    missing_objects: list[str] = []

    for i, tab in enumerate(data.tabs):
        # Add separator between tabs
        if i > 0:
            content_parts.append("\n\n" + "=" * 60 + "\n")

        # Extract tab content
        collected_footnotes: list[tuple[str, str]] = []
        tab_text = _extract_text_from_elements(
            tab.body.get("content", []),
            tab.footnotes,
            collected_footnotes,
            tab.lists,
            None,  # list_counters
            tab.inline_objects,
            unknown_elements,
            missing_objects,
        )

        # Add footnote definitions if any
        tab_text += _render_footnote_definitions(collected_footnotes, tab.footnotes)

        # Add tab title header if content doesn't start with H1
        if not tab_text.lstrip().startswith("# "):
            tab_content = f"# {tab.title}\n\n{tab_text}"
        else:
            tab_content = tab_text

        # Check length limit
        if max_length and (total_length + len(tab_content)) > max_length:
            remaining = max_length - total_length
            if remaining > 100:
                content_parts.append(tab_content[:remaining])
                original = total_length + len(tab_content)
                content_parts.append(
                    f"\n\n[... TRUNCATED at {max_length:,} chars "
                    f"(document is {original:,} chars) ...]"
                )
            data.warnings.append(f"Content truncated at {max_length:,} characters")
            break

        content_parts.append(tab_content)
        total_length += len(tab_content)

    # Aggregate warnings
    if unknown_elements:
        data.warnings.append(f"Unknown element types ignored: {', '.join(sorted(unknown_elements))}")
    if missing_objects:
        data.warnings.append(f"Missing inline objects: {', '.join(missing_objects[:5])}" +
                            (f" (+{len(missing_objects)-5} more)" if len(missing_objects) > 5 else ""))

    return "".join(content_parts).strip()


# =============================================================================
# MARKDOWN ESCAPING
# =============================================================================


def _escape_markdown_link_text(text: str) -> str:
    """Escape characters that break markdown link text syntax."""
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _escape_markdown_url(url: str) -> str:
    """Escape characters that break markdown URL syntax."""
    return url.replace("(", "%28").replace(")", "%29")


def _format_markdown_link(text: str, url: str) -> str:
    """Format text and URL as a markdown link with proper escaping."""
    escaped_text = _escape_markdown_link_text(text)
    escaped_url = _escape_markdown_url(url)
    return f"[{escaped_text}]({escaped_url})"


# =============================================================================
# LIST HANDLING
# =============================================================================


def _get_list_prefix(
    lists: dict[str, Any],
    list_id: str,
    nesting_level: int,
    list_counters: dict[tuple[str, int], int],
) -> str:
    """
    Get the markdown list prefix for a list item.

    Args:
        lists: Document lists definition from Docs API
        list_id: The list ID this paragraph belongs to
        nesting_level: 0-based nesting level
        list_counters: Dict tracking counters per (list_id, level)

    Returns:
        Markdown prefix like "1. ", "- ", "   a. " etc.
    """
    # Initialize counter for this list+level if needed
    counter_key = (list_id, nesting_level)
    if counter_key not in list_counters:
        list_counters[counter_key] = 0
    list_counters[counter_key] += 1
    item_number = list_counters[counter_key]

    # Get list properties
    list_props = lists.get(list_id, {}).get("listProperties", {})
    nesting_levels = list_props.get("nestingLevels", [])

    # Get glyph type for this nesting level
    glyph_type = "BULLET"  # Default to bullet
    if nesting_level < len(nesting_levels):
        level_props = nesting_levels[nesting_level]
        glyph_type = level_props.get("glyphType", "BULLET")

    # Calculate indentation (2 spaces per level)
    indent = "  " * nesting_level

    # Generate prefix based on glyph type
    if glyph_type == "DECIMAL":
        return f"{indent}{item_number}. "
    elif glyph_type == "ZERO_DECIMAL":
        return f"{indent}{item_number:02d}. "
    elif glyph_type == "ALPHA":
        return f"{indent}{_to_alpha(item_number, lowercase=True)}. "
    elif glyph_type == "UPPER_ALPHA":
        return f"{indent}{_to_alpha(item_number, lowercase=False)}. "
    elif glyph_type == "ROMAN":
        return f"{indent}{_to_roman(item_number, lowercase=True)}. "
    elif glyph_type == "UPPER_ROMAN":
        return f"{indent}{_to_roman(item_number, lowercase=False)}. "
    else:
        # BULLET, GLYPH_TYPE_UNSPECIFIED, or custom symbol
        return f"{indent}- "


def _to_alpha(n: int, lowercase: bool = True) -> str:
    """Convert number to alphabetic: 1->a, 26->z, 27->aa, etc."""
    result = ""
    while n > 0:
        n -= 1
        char = chr(ord("a" if lowercase else "A") + (n % 26))
        result = char + result
        n //= 26
    return result


def _to_roman(n: int, lowercase: bool = True) -> str:
    """Convert number to roman numeral."""
    numerals = [
        (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"),
        (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
        (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
    ]
    result = ""
    for value, numeral in numerals:
        while n >= value:
            result += numeral
            n -= value
    return result if lowercase else result.upper()


# =============================================================================
# ELEMENT EXTRACTION
# =============================================================================


def _extract_text_from_elements(
    elements: list[dict[str, Any]],
    footnotes: dict[str, Any] | None = None,
    collected_footnotes: list[tuple[str, str]] | None = None,
    lists: dict[str, Any] | None = None,
    list_counters: dict[tuple[str, int], int] | None = None,
    inline_objects: dict[str, Any] | None = None,
    unknown_elements: set[str] | None = None,
    missing_objects: list[str] | None = None,
) -> str:
    """
    Recursively extract text from document elements.

    Handles:
    - Paragraphs with headings, lists, blockquotes
    - Text runs with bold, italic, strikethrough, monospace
    - Links (merged when adjacent runs have same URL)
    - Footnote references
    - Tables
    - Table of contents
    - Section breaks

    Args:
        elements: List of structural elements from Docs API
        footnotes: Optional dict of footnote ID -> footnote content
        collected_footnotes: Optional list to collect (number, id) tuples
        lists: Optional dict of list definitions
        list_counters: Optional dict tracking list item counters
        unknown_elements: Optional set to track unknown element types
        missing_objects: Optional list to track missing inline objects

    Returns:
        Extracted text as string
    """
    text_parts: list[str] = []

    if list_counters is None:
        list_counters = {}

    # Track previous list state to reset counters when list changes
    prev_list_id = None
    prev_nesting_level = -1

    for element in elements:
        if "paragraph" in element:
            paragraph = element["paragraph"]
            para_text, prev_list_id, prev_nesting_level = _extract_paragraph(
                paragraph,
                lists or {},
                list_counters,
                prev_list_id,
                prev_nesting_level,
                footnotes,
                collected_footnotes,
                inline_objects,
                unknown_elements,
                missing_objects,
            )
            text_parts.append(para_text)

        elif "table" in element:
            table_text = _extract_table(
                element["table"],
                footnotes,
                collected_footnotes,
                lists,
                list_counters,
                inline_objects,
                unknown_elements,
                missing_objects,
            )
            text_parts.append(table_text)

        elif "tableOfContents" in element:
            toc = element["tableOfContents"]
            toc_content = toc.get("content", [])
            text_parts.append(
                _extract_text_from_elements(
                    toc_content, footnotes, collected_footnotes, lists, list_counters,
                    inline_objects, unknown_elements, missing_objects,
                )
            )

        elif "sectionBreak" in element:
            text_parts.append("\n---\n")

        else:
            # Track unknown structural element types
            if unknown_elements is not None:
                elem_type = next((k for k in element.keys() if k != "startIndex" and k != "endIndex"), None)
                if elem_type:
                    unknown_elements.add(elem_type)

    return "".join(text_parts)


def _extract_paragraph(
    paragraph: dict[str, Any],
    lists: dict[str, Any],
    list_counters: dict[tuple[str, int], int],
    prev_list_id: str | None,
    prev_nesting_level: int,
    footnotes: dict[str, Any] | None,
    collected_footnotes: list[tuple[str, str]] | None,
    inline_objects: dict[str, Any] | None = None,
    unknown_elements: set[str] | None = None,
    missing_objects: list[str] | None = None,
) -> tuple[str, str | None, int]:
    """
    Extract text from a paragraph element.

    Returns:
        Tuple of (paragraph_text, new_prev_list_id, new_prev_nesting_level)
    """
    text_parts: list[str] = []

    # Check for list/bullet
    bullet = paragraph.get("bullet")
    list_prefix = ""

    if bullet and lists:
        list_id = bullet.get("listId", "")
        nesting_level = bullet.get("nestingLevel", 0)

        # Reset counters when list changes or going back up levels
        if list_id != prev_list_id:
            keys_to_reset = [k for k in list_counters if k[0] == list_id]
            for k in keys_to_reset:
                del list_counters[k]
        elif nesting_level < prev_nesting_level:
            for level in range(nesting_level + 1, prev_nesting_level + 1):
                counter_key = (list_id, level)
                if counter_key in list_counters:
                    del list_counters[counter_key]

        list_prefix = _get_list_prefix(lists, list_id, nesting_level, list_counters)
        prev_list_id = list_id
        prev_nesting_level = nesting_level
    else:
        prev_list_id = None
        prev_nesting_level = -1

    # Check for heading style
    para_style = paragraph.get("paragraphStyle", {})
    named_style = para_style.get("namedStyleType", "NORMAL_TEXT")
    heading_prefix = {
        "HEADING_1": "# ",
        "HEADING_2": "## ",
        "HEADING_3": "### ",
        "HEADING_4": "#### ",
        "HEADING_5": "##### ",
        "HEADING_6": "###### ",
    }.get(named_style, "")

    # Check for blockquote (indented non-list paragraph)
    blockquote_prefix = ""
    if not bullet:
        indent_start = para_style.get("indentStart", {})
        indent_magnitude = indent_start.get("magnitude", 0)
        if indent_magnitude >= 30:
            nesting_level = max(1, round(indent_magnitude / 36))
            blockquote_prefix = "> " * nesting_level

    # Add prefixes: blockquote > heading > list
    if blockquote_prefix:
        text_parts.append(blockquote_prefix)
    if heading_prefix:
        text_parts.append(heading_prefix)
    if list_prefix:
        text_parts.append(list_prefix)

    # Extract text runs with link merging
    para_content = _extract_paragraph_content(
        paragraph.get("elements", []),
        footnotes,
        collected_footnotes,
        inline_objects,
        unknown_elements,
        missing_objects,
    )
    text_parts.append(para_content)

    return "".join(text_parts), prev_list_id, prev_nesting_level


def _extract_paragraph_content(
    elements: list[dict[str, Any]],
    footnotes: dict[str, Any] | None,
    collected_footnotes: list[tuple[str, str]] | None,
    inline_objects: dict[str, Any] | None = None,
    unknown_elements: set[str] | None = None,
    missing_objects: list[str] | None = None,
) -> str:
    """Extract text from paragraph elements with link merging."""
    text_parts: list[str] = []

    # Buffer for merging adjacent links with same URL
    current_link_url: str | None = None
    current_link_text: list[str] = []

    def flush_link() -> None:
        nonlocal current_link_url, current_link_text
        if current_link_url and current_link_text:
            combined = "".join(current_link_text)
            stripped = combined.rstrip()
            trailing = combined[len(stripped):]
            if stripped:
                text_parts.append(_format_markdown_link(stripped, current_link_url))
            text_parts.append(trailing)
        current_link_url = None
        current_link_text = []

    for elem in elements:
        if "textRun" in elem:
            text_run = elem["textRun"]
            content = text_run.get("content", "")
            text_style = text_run.get("textStyle", {})

            # Apply formatting
            content = _apply_text_formatting(content, text_style)

            # Check for external link
            link_url = text_style.get("link", {}).get("url")

            if link_url and content.strip():
                if link_url == current_link_url:
                    current_link_text.append(content)
                else:
                    flush_link()
                    current_link_url = link_url
                    current_link_text = [content]
            else:
                flush_link()
                text_parts.append(content)

        elif "footnoteReference" in elem:
            flush_link()
            fn_ref = elem["footnoteReference"]
            fn_number = fn_ref.get("footnoteNumber", "?")
            fn_id = fn_ref.get("footnoteId")
            text_parts.append(f"[^{fn_number}]")
            if collected_footnotes is not None and fn_id:
                collected_footnotes.append((fn_number, fn_id))

        elif "inlineObjectElement" in elem:
            flush_link()
            obj_id = elem["inlineObjectElement"].get("inlineObjectId", "")
            text_parts.append(_format_inline_object(obj_id, inline_objects, missing_objects))

        elif "horizontalRule" in elem:
            flush_link()
            text_parts.append("\n---\n")

        elif "pageBreak" in elem:
            flush_link()
            text_parts.append("\n<!-- page break -->\n")

        elif "columnBreak" in elem:
            flush_link()
            text_parts.append("\n<!-- column break -->\n")

        elif "equation" in elem:
            flush_link()
            text_parts.append("[equation]")

        elif "autoText" in elem:
            flush_link()
            auto_type = elem["autoText"].get("type", "UNKNOWN")
            text_parts.append(f"[{auto_type.lower()}]")

        elif "person" in elem:
            flush_link()
            person = elem["person"]
            person_id = person.get("personId", "")
            # personProperties has name if available
            props = person.get("personProperties", {})
            name = props.get("name") or props.get("email") or person_id
            text_parts.append(f"@{name}")

        elif "richLink" in elem:
            flush_link()
            rich_link = elem["richLink"]
            props = rich_link.get("richLinkProperties", {})
            title = props.get("title", "link")
            uri = props.get("uri", "")
            if uri:
                text_parts.append(_format_markdown_link(title, uri))
            else:
                text_parts.append(f"[{title}]")

        elif "dateElement" in elem:
            flush_link()
            # Date is stored as textRun-style content typically
            text_parts.append("[date]")

        else:
            # Track unknown paragraph element types
            if unknown_elements is not None:
                elem_type = next((k for k in elem.keys() if k not in ("startIndex", "endIndex")), None)
                if elem_type:
                    unknown_elements.add(elem_type)

    flush_link()
    return "".join(text_parts)


def _format_inline_object(
    obj_id: str,
    inline_objects: dict[str, Any] | None,
    missing_objects: list[str] | None = None,
) -> str:
    """Format an inline object (image, drawing, chart) as markdown."""
    if not inline_objects or obj_id not in inline_objects:
        if missing_objects is not None:
            missing_objects.append(obj_id)
        return f"[object:{obj_id}]"

    obj = inline_objects[obj_id]
    props = obj.get("inlineObjectProperties", {})
    embedded = props.get("embeddedObject", {})

    title = embedded.get("title", "")
    description = embedded.get("description", "")

    # Determine object type and format appropriately
    # Check linkedContentReference FIRST (if non-empty) - it's more specific than imageProperties
    # Linked charts have both imageProperties AND linkedContentReference
    # Linked slides have linkedContentReference: {} (empty) so fall through to imageProperties
    ref = embedded.get("linkedContentReference", {})
    if ref:  # Non-empty linkedContentReference
        # Sheets chart (currently the only documented linked type)
        sheets_ref = ref.get("sheetsChartReference", {})
        if sheets_ref:
            chart_id = sheets_ref.get("chartId", "")
            spreadsheet_id = sheets_ref.get("spreadsheetId", "")
            label = title or f"Chart {chart_id}"
            return f"[Chart: {label} (from spreadsheet {spreadsheet_id})]"

        # Future-proofing: check for other linked types
        # (Google may add slidesReference, etc.)
        for ref_type in ref.keys():
            label = title or description or ref_type
            return f"[Linked {ref_type}: {label}]"

    # Image (including linked slides which have empty linkedContentReference)
    if "imageProperties" in embedded:
        alt_text = title or description or "image"
        content_uri = embedded["imageProperties"].get("contentUri", "")
        if content_uri:
            return f"![{alt_text}]({content_uri})"
        return f"![{alt_text}]"

    # Google Drawing - can't easily render
    if "embeddedDrawingProperties" in embedded:
        label = title or description or "drawing"
        return f"[Drawing: {label}]"

    # Unknown embedded object type
    label = title or description or obj_id
    return f"[Object: {label}]"


def _apply_text_formatting(content: str | None, text_style: dict[str, Any]) -> str:
    """Apply markdown formatting based on text style."""
    if not content or not content.strip():
        return content or ""

    # Check for monospace font (inline code)
    font_family = text_style.get("weightedFontFamily", {}).get("fontFamily", "")
    monospace_fonts = {
        "Courier New", "Roboto Mono", "Consolas", "Source Code Pro",
        "Monaco", "Menlo", "Fira Code", "JetBrains Mono", "Inconsolata",
    }
    is_monospace = font_family in monospace_fonts

    is_bold = text_style.get("bold", False)
    is_italic = text_style.get("italic", False)
    is_strikethrough = text_style.get("strikethrough", False)

    # Preserve whitespace outside formatting
    leading_ws = content[: len(content) - len(content.lstrip())]
    trailing_ws = content[len(content.rstrip()):]
    inner = content.strip()

    if is_monospace:
        # Code spans don't support nested formatting
        return leading_ws + f"`{inner}`" + trailing_ws
    elif is_bold or is_italic or is_strikethrough:
        if is_italic:
            inner = f"*{inner}*"
        if is_bold:
            inner = f"**{inner}**"
        if is_strikethrough:
            inner = f"~~{inner}~~"
        return leading_ws + inner + trailing_ws

    return content


def _extract_table(
    table: dict[str, Any],
    footnotes: dict[str, Any] | None,
    collected_footnotes: list[tuple[str, str]] | None,
    lists: dict[str, Any] | None,
    list_counters: dict[tuple[str, int], int] | None,
    inline_objects: dict[str, Any] | None = None,
    unknown_elements: set[str] | None = None,
    missing_objects: list[str] | None = None,
) -> str:
    """Extract table as markdown."""
    rows = table.get("tableRows", [])
    if not rows:
        return ""

    table_lines: list[str] = []

    for row_idx, row in enumerate(rows):
        cells = row.get("tableCells", [])
        cell_texts: list[str] = []

        for cell in cells:
            cell_content = cell.get("content", [])
            cell_text = _extract_text_from_elements(
                cell_content, footnotes, collected_footnotes, lists, list_counters,
                inline_objects, unknown_elements, missing_objects,
            )
            # Clean for table cell: strip, collapse newlines, escape pipes
            cell_text = cell_text.strip().replace("\n", " ").replace("|", "\\|")
            cell_texts.append(cell_text)

        # Build row: | cell1 | cell2 | cell3 |
        table_lines.append("| " + " | ".join(cell_texts) + " |")

        # Add header separator after first row
        if row_idx == 0:
            table_lines.append("|" + "|".join(["---"] * len(cell_texts)) + "|")

    return "\n".join(table_lines) + "\n\n"


def _render_footnote_definitions(
    collected_footnotes: list[tuple[str, str]],
    footnotes: dict[str, Any],
) -> str:
    """Render markdown footnote definitions from collected references."""
    if not collected_footnotes or not footnotes:
        return ""

    definitions: list[str] = []
    seen: set[str] = set()

    for fn_number, fn_id in collected_footnotes:
        if fn_id in seen:
            continue
        seen.add(fn_id)

        fn_data = footnotes.get(fn_id)
        if not fn_data:
            continue

        fn_content = fn_data.get("content", [])
        fn_text = _extract_text_from_elements(fn_content).strip()

        if fn_text:
            definitions.append(f"[^{fn_number}]: {fn_text}")

    if not definitions:
        return ""

    return "\n\n---\n" + "\n".join(definitions) + "\n"
