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

    Populates data.warnings with extraction issues encountered.

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
    # Initialize warnings (will aggregate from slides)
    data.warnings = []

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

    # Aggregate warnings from all slides
    for slide in data.slides:
        for warning in slide.warnings:
            data.warnings.append(f"Slide {slide.index + 1}: {warning}")

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
# THUMBNAIL DECISION LOGIC
# =============================================================================
# Selective thumbnailing: skip stock photos and text-only slides.
# Ported from v1 (mcp-google-workspace).


def _get_image_coverage(
    element: dict[str, Any],
    page_width: int,
    page_height: int,
) -> float:
    """
    Calculate what fraction of the page an image element covers.

    Args:
        element: PageElement containing an image
        page_width: Page width in EMU
        page_height: Page height in EMU

    Returns:
        Coverage ratio (0.0 to 1.0)
    """
    if page_width <= 0 or page_height <= 0:
        return 0.0

    # Get element size from transform
    transform = element.get("transform", {})
    # Size can be in scaleX/scaleY (relative) or in the size property
    size = element.get("size", {})

    elem_width: float = size.get("width", {}).get("magnitude", 0)
    elem_height: float = size.get("height", {}).get("magnitude", 0)

    if elem_width <= 0 or elem_height <= 0:
        return 0.0

    page_area: float = page_width * page_height
    elem_area: float = elem_width * elem_height

    return elem_area / page_area


def _is_single_large_image_slide(
    elements: list[dict[str, Any]],
    page_width: int,
    page_height: int,
    coverage_threshold: float = 0.5,
) -> bool:
    """
    Detect if a slide is dominated by a single large image (likely stock photo).

    Args:
        elements: List of pageElements from slide
        page_width: Page width in EMU
        page_height: Page height in EMU
        coverage_threshold: Minimum fraction of page for "large" (default 0.5)

    Returns:
        True if slide has exactly one image covering > threshold of page
    """
    image_elements = []
    for element in elements:
        if "image" in element:
            coverage = _get_image_coverage(element, page_width, page_height)
            image_elements.append((element, coverage))

    # Single large image check
    if len(image_elements) == 1:
        _, coverage = image_elements[0]
        return coverage >= coverage_threshold

    return False


def _has_fragmented_text(
    text_content: list[str],
    min_fragments: int = 5,
    max_avg_length: int = 50,
) -> bool:
    """
    Detect if text is fragmented (many short pieces) suggesting visual layout matters.

    Fragmented text = spatial arrangement carries meaning lost in extraction.

    Args:
        text_content: List of extracted text strings
        min_fragments: Minimum number of text pieces to consider fragmented
        max_avg_length: Maximum average length to consider fragmented

    Returns:
        True if text appears fragmented (thumbnail would help)
    """
    if len(text_content) < min_fragments:
        return False

    avg_length = sum(len(t) for t in text_content) / len(text_content)
    return avg_length < max_avg_length


def _determine_thumbnail_need(
    elements: list[dict[str, Any]],
    text_content: list[str],
    visual_elements: list[str],
    page_width: int,
    page_height: int,
) -> tuple[bool, str | None, str | None]:
    """
    Decide whether a slide needs a thumbnail.

    Returns:
        Tuple of (needs_thumbnail, thumbnail_reason, skip_reason)
        - If needs_thumbnail=True: reason explains why (chart, image, fragmented_text)
        - If needs_thumbnail=False: skip_reason explains why (single_large_image, text_only)
    """
    # Check for visual elements
    has_visuals = len(visual_elements) > 0

    if has_visuals:
        # Is it just a stock photo?
        if _is_single_large_image_slide(elements, page_width, page_height):
            return False, None, "single_large_image"

        # Has meaningful visual content - determine reason
        reason = "shapes"  # default
        for elem in visual_elements:
            if "[CHART]" in elem:
                reason = "chart"
                break
            elif "[IMAGE]" in elem:
                reason = "image"

        return True, reason, None

    # No visual elements - check for fragmented text
    if _has_fragmented_text(text_content):
        return True, "fragmented_text", None

    # Text-only slide, thumbnail not needed
    return False, None, "text_only"


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

    # Extract page dimensions for thumbnail decisions
    page_width = page_size.get("width", {}).get("magnitude", 0) if page_size else 0
    page_height = page_size.get("height", {}).get("magnitude", 0) if page_size else 0

    slides: list[SlideData] = []
    for idx, slide_data in enumerate(response.get("slides", [])):
        slides.append(_parse_slide(slide_data, idx, page_width, page_height))

    return PresentationData(
        title=title,
        presentation_id=presentation_id,
        slides=slides,
        page_size=page_size,
        locale=locale,
    )


def _parse_slide(
    slide: dict[str, Any],
    index: int,
    page_width: int = 0,
    page_height: int = 0,
) -> SlideData:
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

    # Determine thumbnail need
    elements = slide.get("pageElements", [])
    needs_thumb, thumb_reason, skip_reason = _determine_thumbnail_need(
        elements, text_content, visual_elements, page_width, page_height
    )

    return SlideData(
        slide_id=slide_id,
        index=index,
        title=title,
        text_content=text_content,
        tables=tables,
        notes=notes,
        visual_elements=visual_elements,
        warnings=warnings,
        needs_thumbnail=needs_thumb,
        thumbnail_reason=thumb_reason,
        skip_thumbnail_reason=skip_reason,
    )


def _parse_table(table: dict[str, Any]) -> SlideTable:
    """
    Parse a table element into SlideTable.

    Handles merged cells by expanding them to fill the grid:
    - colSpan: A cell spanning 3 columns becomes 1 cell + 2 empty cells
    - rowSpan: Subsequent rows get empty cells inserted where the span occupies

    The Slides API only includes cells in the row where they start. If a cell
    has rowSpan=3, rows 2 and 3 won't have that cell at all — we must track
    which columns are "occupied" and insert placeholders.
    """
    num_columns = table.get("columns", 0)
    rows: list[list[str]] = []

    # Track how many more rows each column is occupied by a rowSpan.
    # Key = column index, Value = remaining rows (decremented after each row)
    row_span_remaining: dict[int, int] = {}

    for row in table.get("tableRows", []):
        row_cells: list[str] = []
        col_index = 0  # Current position in the output row
        cell_iter = iter(row.get("tableCells", []))

        while col_index < num_columns:
            # Check if this column is occupied by a rowSpan from a previous row
            if col_index in row_span_remaining and row_span_remaining[col_index] > 0:
                row_cells.append("")  # Placeholder for spanned cell
                col_index += 1
                continue

            # Get next cell from this row
            cell = next(cell_iter, None)
            if cell is None:
                # No more cells in this row, pad with empty
                row_cells.append("")
                col_index += 1
                continue

            # Extract cell text
            cell_text = ""
            if "text" in cell:
                text_elements = cell["text"].get("textElements", [])
                cell_text = _extract_text_from_elements(text_elements)
            cell_text = cell_text.strip()

            col_span = cell.get("columnSpan", 1)
            row_span = cell.get("rowSpan", 1)

            # Add the cell content
            row_cells.append(cell_text)

            # Track rowSpan for subsequent rows (all columns this cell spans)
            if row_span > 1:
                for span_col in range(col_index, col_index + col_span):
                    row_span_remaining[span_col] = row_span - 1

            col_index += 1

            # Handle colSpan by adding empty cells
            for _ in range(col_span - 1):
                row_cells.append("")
                col_index += 1

        rows.append(row_cells)

        # Decrement rowSpan counters for next row
        for col in list(row_span_remaining.keys()):
            row_span_remaining[col] -= 1
            if row_span_remaining[col] <= 0:
                del row_span_remaining[col]

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
            # .get("content", "") returns None when key exists with null value
            content = element["textRun"].get("content") or ""
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
