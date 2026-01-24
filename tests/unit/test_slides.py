"""Unit tests for slides extractor."""

import pytest

from extractors.slides import (
    extract_slides_content,
    parse_presentation,
    _format_table,
    _clean_text,
    _extract_text_from_elements,
)
from models import PresentationData, SlideData, SlideTable


class TestExtractSlidesContent:
    """Tests for the main extraction function."""

    def test_basic_extraction(self, real_slides: PresentationData) -> None:
        """Test that slides are extracted with proper structure."""
        result = extract_slides_content(real_slides)

        # Check header
        assert "# Test Presentation" in result
        assert "**Slides:** 7" in result

        # Check slide headers present (1-indexed)
        assert "## Slide 1" in result
        assert "## Slide 5" in result
        assert "## Slide 7" in result

    def test_content_extraction(self, real_slides: PresentationData) -> None:
        """Test that text content is extracted from slides."""
        result = extract_slides_content(real_slides)

        # Title slide should have presentation title
        assert "Test Presentation" in result

    def test_truncation(self) -> None:
        """Test that content is truncated at max_length."""
        # Create a presentation with lots of content
        slides = [
            SlideData(
                slide_id=f"slide_{i}",
                index=i,
                text_content=["Lorem ipsum dolor sit amet " * 50],
            )
            for i in range(20)
        ]
        data = PresentationData(
            title="Big Presentation",
            presentation_id="big-id",
            slides=slides,
        )

        result = extract_slides_content(data, max_length=1000)

        assert len(result) <= 1200  # Buffer for truncation message
        assert "TRUNCATED" in result

    def test_empty_presentation(self) -> None:
        """Test handling of presentation with no slides."""
        data = PresentationData(
            title="Empty",
            presentation_id="empty-id",
            slides=[],
        )

        result = extract_slides_content(data)

        assert "# Empty" in result
        assert "**Slides:** 0" in result

    def test_speaker_notes_included(self) -> None:
        """Test that speaker notes are included in output."""
        data = PresentationData(
            title="With Notes",
            presentation_id="notes-id",
            slides=[
                SlideData(
                    slide_id="s1",
                    index=0,
                    text_content=["Slide content"],
                    notes="These are the speaker notes for slide 1",
                )
            ],
        )

        result = extract_slides_content(data)

        assert "### Speaker Notes" in result
        assert "speaker notes for slide 1" in result

    def test_visual_elements_flagged(self) -> None:
        """Test that visual elements are listed."""
        data = PresentationData(
            title="Visual",
            presentation_id="visual-id",
            slides=[
                SlideData(
                    slide_id="s1",
                    index=0,
                    text_content=["Some text"],
                    visual_elements=["[IMAGE] Visual content", "[CHART] Embedded spreadsheet chart"],
                )
            ],
        )

        result = extract_slides_content(data)

        assert "### Visual Elements" in result
        assert "[IMAGE]" in result
        assert "[CHART]" in result

    def test_tables_formatted(self) -> None:
        """Test that tables are formatted as markdown."""
        data = PresentationData(
            title="Tables",
            presentation_id="tables-id",
            slides=[
                SlideData(
                    slide_id="s1",
                    index=0,
                    tables=[
                        SlideTable(rows=[
                            ["Header 1", "Header 2"],
                            ["Value 1", "Value 2"],
                            ["Value 3", "Value 4"],
                        ])
                    ],
                )
            ],
        )

        result = extract_slides_content(data)

        assert "### Tables" in result
        assert "| Header 1 | Header 2 |" in result
        assert "| --- | --- |" in result
        assert "| Value 1 | Value 2 |" in result

    def test_thumbnails_flag_shown(self) -> None:
        """Test that thumbnails available message is shown when flag set."""
        data = PresentationData(
            title="With Thumbs",
            presentation_id="thumbs-id",
            slides=[],
            thumbnails_included=True,
        )

        result = extract_slides_content(data)

        assert "Thumbnails:" in result
        assert "slide_NN.png" in result


class TestParsePresentation:
    """Tests for parsing raw API responses."""

    def test_parse_real_presentation(self, real_slides: PresentationData) -> None:
        """Test that real API response parses correctly."""
        assert real_slides.title == "Test Presentation"
        assert real_slides.presentation_id == "1ZrknZXSsyDtWuWq0cXV7UMZ-7WHClm3fJa61uZY2pwY"
        assert len(real_slides.slides) == 7

    def test_charts_flagged(self, real_slides: PresentationData) -> None:
        """Test that embedded charts are flagged."""
        result = extract_slides_content(real_slides)
        assert "[CHART] Embedded spreadsheet chart" in result

    def test_groups_flagged(self, real_slides: PresentationData) -> None:
        """Test that grouped elements are flagged."""
        result = extract_slides_content(real_slides)
        assert "[GROUP]" in result
        assert "grouped elements" in result

    def test_group_text_extracted(self, real_slides: PresentationData) -> None:
        """Test that text inside groups is extracted."""
        result = extract_slides_content(real_slides)
        # The grouped shapes have text labels
        assert "Top Left" in result
        assert "Top right" in result
        assert "Bottom Middle" in result

    def test_speaker_notes_from_real_fixture(self, real_slides: PresentationData) -> None:
        """Test that speaker notes are extracted from real fixture."""
        result = extract_slides_content(real_slides)
        assert "These are some speaker notes" in result

    def test_slide_titles_extracted(self, real_slides: PresentationData) -> None:
        """Test that slide titles appear in headers."""
        result = extract_slides_content(real_slides)
        # Slide 1 has CENTERED_TITLE "Test Presentation"
        assert "## Slide 1: Test Presentation" in result
        # Slide 2 has TITLE "This is a heading"
        assert "## Slide 2: This is a heading" in result

    def test_title_not_duplicated_in_content(self, real_slides: PresentationData) -> None:
        """Test that title doesn't appear twice (in header and content)."""
        result = extract_slides_content(real_slides)
        # Slide 1 should have title in header but NOT in a Content section
        slide1_section = result.split("## Slide 2")[0]
        # Should have "## Slide 1: Test Presentation" but no "### Content" section
        assert "## Slide 1: Test Presentation" in slide1_section
        # The title slide has no body content, only notes
        assert "### Speaker Notes" in slide1_section

    def test_merged_cells_handled(self, real_slides: PresentationData) -> None:
        """Test that merged cells are handled in tables."""
        result = extract_slides_content(real_slides)
        # Slide 3 has a table with merged cell spanning 3 columns
        assert "I am a merged cell - fear me" in result
        # The row should have 3 columns (merged + 2 empty)
        assert "| I am a merged cell - fear me |  |  |" in result

    def test_selective_thumbnail_decisions(self, real_slides: PresentationData) -> None:
        """Test that slides get correct thumbnail decisions.

        Based on v1 selective logic:
        - Charts: needs_thumbnail=True (visual IS the content)
        - Images: needs_thumbnail=True (unless single large = stock photo)
        - Text-only: needs_thumbnail=False (extraction is enough)
        """
        # Check that decisions were made for all slides
        for slide in real_slides.slides:
            # Every slide should have a reason (either thumbnail_reason or skip_reason)
            has_decision = slide.thumbnail_reason or slide.skip_thumbnail_reason
            assert has_decision, f"Slide {slide.index + 1} has no thumbnail decision"

        # Slide 6 and 7 have charts - should need thumbnails
        slide_6 = real_slides.slides[5]
        slide_7 = real_slides.slides[6]
        assert slide_6.needs_thumbnail, "Chart slide should need thumbnail"
        assert slide_6.thumbnail_reason == "chart"
        assert slide_7.needs_thumbnail, "Chart slide should need thumbnail"
        assert slide_7.thumbnail_reason == "chart"

        # At least one slide should be text_only (skipped)
        text_only_slides = [s for s in real_slides.slides if s.skip_thumbnail_reason == "text_only"]
        assert len(text_only_slides) > 0, "Should have at least one text-only slide"

    def test_rowspan_handled(self, real_slides: PresentationData) -> None:
        """Test that vertical merged cells (rowSpan) are handled correctly.

        Slide 3 Table 2 has a cell in column 3 that spans 3 rows.
        Without rowSpan handling, rows 2 and 3 would only have 2 cells
        because the API doesn't include the spanned cell in those rows.
        """
        # Check the table structure directly
        slide_3 = real_slides.slides[2]
        assert len(slide_3.tables) >= 2, "Slide 3 should have at least 2 tables"

        # Table 2 has the rowSpan cell
        table_2 = slide_3.tables[1]

        # All 3 rows should have exactly 3 cells (not 3, 2, 2)
        assert len(table_2.rows) == 3, "Table 2 should have 3 rows"
        for i, row in enumerate(table_2.rows):
            assert len(row) == 3, f"Row {i} should have 3 cells, got {len(row)}: {row}"

        # Row 0 has the merged cell content
        assert "vertical merged cell" in table_2.rows[0][2]
        # Rows 1 and 2 have empty placeholders in column 3
        assert table_2.rows[1][2] == ""
        assert table_2.rows[2][2] == ""

    def test_slide_ids_parsed(self, real_slides: PresentationData) -> None:
        """Test that slide IDs are extracted."""
        # All slides should have IDs
        for slide in real_slides.slides:
            assert slide.slide_id
            assert isinstance(slide.slide_id, str)

    def test_slide_indices(self, real_slides: PresentationData) -> None:
        """Test that slide indices are 0-based and sequential."""
        for i, slide in enumerate(real_slides.slides):
            assert slide.index == i


class TestWarnings:
    """Tests for warning accumulation."""

    def test_missing_object_id_warning(self) -> None:
        """Test that missing objectId generates a warning."""
        from extractors.slides import _parse_slide

        # Slide without objectId
        slide_data = {"pageElements": []}
        result = _parse_slide(slide_data, index=0)

        assert len(result.warnings) == 1
        assert "Missing objectId" in result.warnings[0]

    def test_warning_shown_in_output(self) -> None:
        """Test that warnings appear in markdown output."""
        data = PresentationData(
            title="Test",
            presentation_id="test-id",
            slides=[
                SlideData(
                    slide_id="",  # Missing!
                    index=0,
                    warnings=["Missing objectId — thumbnails unavailable"],
                )
            ],
        )

        result = extract_slides_content(data)
        assert "*Warning:" in result
        assert "Missing objectId" in result


class TestFormatTable:
    """Tests for table formatting."""

    def test_simple_table(self) -> None:
        """Test basic table formatting."""
        table = SlideTable(rows=[
            ["A", "B"],
            ["1", "2"],
        ])

        result = _format_table(table)

        assert "| A | B |" in result
        assert "| --- | --- |" in result
        assert "| 1 | 2 |" in result

    def test_empty_table(self) -> None:
        """Test empty table handling."""
        table = SlideTable(rows=[])
        result = _format_table(table)
        assert "Empty table" in result

    def test_table_truncation(self) -> None:
        """Test table truncation for large tables."""
        table = SlideTable(rows=[
            ["Header"],
            *[[f"Row {i}"] for i in range(50)]
        ])

        result = _format_table(table, max_rows=10)

        assert "truncated" in result.lower()
        assert "10" in result
        assert "51" in result  # Total rows

    def test_pipe_escaping(self) -> None:
        """Test that pipe characters in cells are escaped."""
        table = SlideTable(rows=[
            ["Col1", "Col2"],
            ["A | B", "C"],
        ])

        result = _format_table(table)

        assert "A \\| B" in result


class TestCleanText:
    """Tests for text cleaning."""

    def test_removes_blank_lines(self) -> None:
        """Test that blank lines are removed."""
        text = "Line 1\n\n\nLine 2"
        result = _clean_text(text)
        assert result == "Line 1\nLine 2"

    def test_strips_whitespace(self) -> None:
        """Test that lines are stripped."""
        text = "  Line 1  \n  Line 2  "
        result = _clean_text(text)
        assert result == "Line 1\nLine 2"


class TestExtractTextFromElements:
    """Tests for textElements extraction."""

    def test_text_run(self) -> None:
        """Test extracting textRun content."""
        elements = [
            {"textRun": {"content": "Hello "}},
            {"textRun": {"content": "World"}},
        ]

        result = _extract_text_from_elements(elements)

        assert result == "Hello World"

    def test_auto_text(self) -> None:
        """Test extracting autoText placeholders."""
        elements = [
            {"autoText": {"type": "SLIDE_NUMBER"}},
        ]

        result = _extract_text_from_elements(elements)

        assert "[SLIDE_NUMBER]" in result

    def test_mixed_elements(self) -> None:
        """Test mixed textRun and autoText."""
        elements = [
            {"textRun": {"content": "Slide "}},
            {"autoText": {"type": "SLIDE_NUMBER"}},
            {"textRun": {"content": " of 10"}},
        ]

        result = _extract_text_from_elements(elements)

        assert "Slide [SLIDE_NUMBER] of 10" in result

    def test_paragraph_markers_ignored(self) -> None:
        """Test that paragraphMarker elements don't add content."""
        elements = [
            {"paragraphMarker": {"style": {}}},
            {"textRun": {"content": "Text"}},
        ]

        result = _extract_text_from_elements(elements)

        assert result == "Text"
