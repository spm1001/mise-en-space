"""
Slides Extractor — Pure function for converting presentation data to markdown.

Receives pre-assembled presentation data, returns markdown output.
No API calls, no MCP awareness.

Thumbnails are handled separately by the workspace layer (written as PNGs).
This extractor focuses on text content only.
"""

from typing import Any

from models import PresentationData, SlideData, SlideTable


def extract_slides_content(
    data: PresentationData,
    max_length: int | None = None,
) -> str:
    """
    Convert presentation data to markdown text.

    Args:
        data: PresentationData with title, slides, and metadata
        max_length: Optional character limit. Truncates if exceeded.

    Returns:
        Markdown text with slide structure:
            # Presentation Title

            **Slides:** 10

            ---

            ## Slide 1

            ### Content
            Text from shapes...

            ### Speaker Notes
            Notes text...

            ---

            ## Slide 2
            ...
    """
    parts: list[str] = []

    # Header
    parts.append(f"# {data.title}")
    parts.append(f"\n**Slides:** {len(data.slides)}")
    if data.thumbnails_included:
        parts.append("**Thumbnails:** Available as slide_NN.png")
    parts.append("\n---\n")

    total_length = sum(len(p) for p in parts)

    # Process each slide
    for slide in data.slides:
        slide_md = _format_slide(slide)

        # Check length limit
        if max_length and (total_length + len(slide_md)) > max_length:
            remaining = max_length - total_length
            if remaining > 100:
                parts.append(slide_md[:remaining])
            parts.append(f"\n\n[... TRUNCATED at {max_length:,} chars ...]")
            break

        parts.append(slide_md)
        total_length += len(slide_md)

    return "".join(parts)


def _format_slide(slide: SlideData) -> str:
    """Format a single slide as markdown."""
    parts: list[str] = []

    # Slide header (1-indexed for humans) with title if available
    if slide.title:
        parts.append(f"\n## Slide {slide.index + 1}: {slide.title}\n")
    else:
        parts.append(f"\n## Slide {slide.index + 1}\n")

    # Warnings (if any)
    if slide.warnings:
        for warning in slide.warnings:
            parts.append(f"*Warning: {warning}*\n\n")

    # Text content
    if slide.text_content:
        parts.append("### Content\n")
        for text in slide.text_content:
            # Clean up excessive whitespace
            cleaned = _clean_text(text)
            if cleaned:
                parts.append(cleaned)
                parts.append("\n\n")

    # Tables
    if slide.tables:
        parts.append("### Tables\n")
        for i, table in enumerate(slide.tables, 1):
            if len(slide.tables) > 1:
                parts.append(f"**Table {i}:**\n")
            parts.append(_format_table(table))
            parts.append("\n\n")

    # Speaker notes
    if slide.notes:
        cleaned_notes = _clean_text(slide.notes)
        if cleaned_notes:
            parts.append("### Speaker Notes\n")
            parts.append(cleaned_notes)
            parts.append("\n\n")

    # Visual elements (informational - thumbnails available separately)
    if slide.visual_elements:
        parts.append("### Visual Elements\n")
        for elem in slide.visual_elements:
            parts.append(f"- {elem}\n")
        parts.append("\n")

    parts.append("---\n")

    return "".join(parts)


def _clean_text(text: str) -> str:
    """Clean up text - remove excessive whitespace while preserving structure."""
    lines = text.split("\n")
    cleaned_lines = [line.strip() for line in lines if line.strip()]
    return "\n".join(cleaned_lines)


def _format_table(table: SlideTable, max_rows: int = 20) -> str:
    """Format table as markdown."""
    rows = table.rows
    if not rows or not rows[0]:
        return "*Empty table*"

    # Truncate if too many rows
    truncated = False
    total_rows = len(rows)
    if total_rows > max_rows:
        rows = rows[:max_rows]
        truncated = True

    lines: list[str] = []

    # Header row
    header = rows[0]
    lines.append("| " + " | ".join(_escape_cell(c) for c in header) + " |")

    # Separator
    lines.append("| " + " | ".join("---" for _ in header) + " |")

    # Data rows
    for row in rows[1:]:
        # Pad if uneven columns
        while len(row) < len(header):
            row.append("")
        lines.append("| " + " | ".join(_escape_cell(c) for c in row) + " |")

    if truncated:
        lines.append(f"\n*Table truncated: showing {max_rows} of {total_rows} rows*")

    return "\n".join(lines)


def _escape_cell(value: str) -> str:
    """Escape pipe characters in table cells."""
    return value.replace("|", "\\|").replace("\n", " ")


# =============================================================================
# RAW API RESPONSE PARSING
# =============================================================================
# These functions parse raw Slides API responses into our model types.
# Used by the adapter layer to assemble PresentationData.


def parse_presentation(response: dict[str, Any]) -> PresentationData:
    """
    Parse a presentations().get() response into PresentationData.

    This is the bridge between raw API response and our typed model.
    Called by the adapter after fetching from Google API.
    """
    title = response.get("title", "Untitled Presentation")
    presentation_id = response.get("presentationId", "")
    page_size = response.get("pageSize")
    locale = response.get("locale")

    slides: list[SlideData] = []
    for idx, slide_data in enumerate(response.get("slides", [])):
        slides.append(_parse_slide(slide_data, idx))

    return PresentationData(
        title=title,
        presentation_id=presentation_id,
        slides=slides,
        page_size=page_size,
        locale=locale,
    )


def _parse_slide(slide: dict[str, Any], index: int) -> SlideData:
    """Parse a single slide from API response."""
    slide_id = slide.get("objectId", "")
    title: str | None = None
    text_content: list[str] = []
    tables: list[SlideTable] = []
    visual_elements: list[str] = []
    notes: str | None = None
    warnings: list[str] = []

    # Check for missing objectId
    if not slide_id:
        warnings.append("Missing objectId — thumbnails unavailable for this slide")

    # Title placeholder types
    TITLE_PLACEHOLDERS = {"TITLE", "CENTERED_TITLE"}

    # Process page elements (including recursion into groups)
    def process_element(element: dict[str, Any], is_group_child: bool = False) -> None:
        nonlocal title, text_content, tables, visual_elements

        # Recurse into groups
        if "elementGroup" in element:
            group = element["elementGroup"]
            child_count = len(group.get("children", []))
            visual_elements.append(f"[GROUP] {child_count} grouped elements (likely diagram)")
            for child in group.get("children", []):
                process_element(child, is_group_child=True)
            return

        # Extract tables
        if "table" in element:
            table = _parse_table(element["table"])
            if table.rows:
                tables.append(table)
            return

        # Extract text from shapes
        if "shape" in element:
            shape = element["shape"]
            placeholder = shape.get("placeholder", {})
            placeholder_type = placeholder.get("type", "")

            text = _extract_text_from_shape(shape)
            if text:
                # Check if this is a title placeholder (and not in a group)
                if placeholder_type in TITLE_PLACEHOLDERS and not is_group_child:
                    title = text
                else:
                    text_content.append(text)

        # Flag visual elements
        visual_desc = _get_visual_description(element)
        if visual_desc:
            visual_elements.append(visual_desc)

    for element in slide.get("pageElements", []):
        process_element(element)

    # Extract speaker notes
    notes_page = slide.get("slideProperties", {}).get("notesPage", {})
    for element in notes_page.get("pageElements", []):
        if "shape" in element:
            shape = element["shape"]
            placeholder = shape.get("placeholder", {})
            if placeholder.get("type") == "BODY":
                notes = _extract_text_from_shape(shape)
                break

    return SlideData(
        slide_id=slide_id,
        index=index,
        title=title,
        text_content=text_content,
        tables=tables,
        notes=notes,
        visual_elements=visual_elements,
        warnings=warnings,
    )


def _parse_table(table: dict[str, Any]) -> SlideTable:
    """
    Parse a table element into SlideTable.

    Handles merged cells by expanding them to fill the grid.
    A cell with colSpan=3 becomes 3 cells in the output row.
    """
    num_columns = table.get("columns", 0)
    rows: list[list[str]] = []

    for row in table.get("tableRows", []):
        row_cells: list[str] = []
        for cell in row.get("tableCells", []):
            cell_text = ""
            if "text" in cell:
                text_elements = cell["text"].get("textElements", [])
                cell_text = _extract_text_from_elements(text_elements)
            cell_text = cell_text.strip()

            # Handle column spans by repeating/padding
            col_span = cell.get("columnSpan", 1)
            row_cells.append(cell_text)
            # Add empty cells for the span (merged cell shows content once)
            for _ in range(col_span - 1):
                row_cells.append("")

        # Ensure row has correct number of columns
        while len(row_cells) < num_columns:
            row_cells.append("")

        rows.append(row_cells)

    return SlideTable(rows=rows)


def _extract_text_from_shape(shape: dict[str, Any]) -> str:
    """Extract text content from a shape."""
    text_obj = shape.get("text", {})
    text_elements = text_obj.get("textElements", [])
    return _extract_text_from_elements(text_elements)


def _extract_text_from_elements(elements: list[dict[str, Any]]) -> str:
    """Extract text from textElements array."""
    parts: list[str] = []

    for element in elements:
        if "textRun" in element:
            content = element["textRun"].get("content", "")
            parts.append(content)
        elif "autoText" in element:
            # Auto text like slide numbers, dates
            auto_type = element["autoText"].get("type", "UNKNOWN")
            parts.append(f"[{auto_type}]")

    return "".join(parts).strip()


def _get_visual_description(element: dict[str, Any]) -> str | None:
    """Get description of visual elements for context."""
    if "image" in element:
        image = element["image"]
        source_url = image.get("sourceUrl", "")
        if "placeholder" in source_url.lower():
            return None
        return "[IMAGE] Visual content"

    if "sheetsChart" in element:
        chart = element["sheetsChart"]
        chart_id = chart.get("chartId", "unknown")
        return f"[CHART] Embedded spreadsheet chart (ID: {chart_id})"

    if "video" in element:
        video = element["video"]
        video_url = video.get("url", "")
        return f"[VIDEO] Embedded video: {video_url}"

    if "wordArt" in element:
        return "[WORDART] Stylized text"

    if "line" in element:
        line = element["line"]
        line_type = line.get("lineType", "STRAIGHT")
        connectors = [
            "CURVED_CONNECTOR_2", "CURVED_CONNECTOR_3", "CURVED_CONNECTOR_4",
            "STRAIGHT_CONNECTOR_1", "BENT_CONNECTOR_2", "BENT_CONNECTOR_3",
        ]
        if line_type in connectors:
            return "[CONNECTOR] Visual connector"

    # Shapes without text might be diagram elements
    if "shape" in element:
        shape = element["shape"]
        shape_type = shape.get("shapeType", "RECTANGLE")
        text_content = shape.get("text", {})
        if not text_content and shape_type not in ("TEXT_BOX", "RECTANGLE"):
            return f"[SHAPE] {shape_type}"

    return None
