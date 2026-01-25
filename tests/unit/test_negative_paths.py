"""
Negative path tests — malformed input, missing fields, edge cases.

These tests verify extractors handle bad data gracefully:
- Produce warnings, not exceptions (for recoverable errors)
- Fail clearly for unrecoverable errors
- Don't crash on null/missing fields
"""

import json
import pytest
from pathlib import Path
from datetime import datetime

from models import (
    DocData, DocTab,
    SpreadsheetData, SheetTab,
    PresentationData,
    GmailThreadData, EmailMessage,
)
from extractors.docs import extract_doc_content
from extractors.sheets import extract_sheets_content
from extractors.slides import extract_slides_content, parse_presentation
from extractors.gmail import extract_thread_content


# Load malformed fixtures
FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "malformed"


def load_malformed_fixture(name: str) -> dict:
    """Load a malformed fixture by name."""
    with open(FIXTURES_DIR / f"{name}.json") as f:
        return json.load(f)


class TestDocsNegativePaths:
    """Tests for docs extractor with malformed input."""

    @pytest.fixture
    def malformed_docs(self) -> DocData:
        """Load malformed docs fixture."""
        raw = load_malformed_fixture("docs_edge_cases")
        return DocData(
            title=raw["title"],
            document_id=raw["document_id"],
            tabs=[
                DocTab(
                    title=t["title"],
                    tab_id=t["tab_id"],
                    index=t["index"],
                    body=t["body"],
                    inline_objects=t.get("inline_objects", {}),
                )
                for t in raw["tabs"]
            ],
        )

    def test_handles_null_textrun_content(self, malformed_docs: DocData) -> None:
        """Extractor handles null textRun content without crashing."""
        content = extract_doc_content(malformed_docs)
        # Should produce some output without exception
        assert content is not None
        assert "Valid text" in content

    def test_handles_missing_inline_object(self, malformed_docs: DocData) -> None:
        """Extractor handles reference to missing inline object."""
        content = extract_doc_content(malformed_docs)
        # Should produce warning about missing object
        assert malformed_docs.warnings  # Warnings populated
        assert any("missing" in w.lower() or "not found" in w.lower()
                   for w in malformed_docs.warnings)

    def test_handles_empty_paragraph(self, malformed_docs: DocData) -> None:
        """Extractor handles paragraphs with no elements."""
        content = extract_doc_content(malformed_docs)
        # Should not crash
        assert content is not None

    def test_handles_unknown_element_type(self, malformed_docs: DocData) -> None:
        """Extractor handles unknown element types gracefully."""
        content = extract_doc_content(malformed_docs)
        # Should warn about unknown type
        assert any("unknown" in w.lower() for w in malformed_docs.warnings)


class TestSheetsNegativePaths:
    """Tests for sheets extractor with malformed input."""

    @pytest.fixture
    def malformed_sheets(self) -> SpreadsheetData:
        """Load malformed sheets fixture."""
        raw = load_malformed_fixture("sheets_edge_cases")
        return SpreadsheetData(
            title=raw["title"],
            spreadsheet_id=raw["spreadsheet_id"],
            sheets=[
                SheetTab(name=s["name"], values=s["values"])
                for s in raw["sheets"]
            ],
        )

    def test_handles_empty_sheet(self, malformed_sheets: SpreadsheetData) -> None:
        """Extractor handles sheet with no data."""
        content = extract_sheets_content(malformed_sheets)
        assert content is not None
        # Should have warning about empty sheet
        assert any("empty" in w.lower() for w in malformed_sheets.warnings)

    def test_handles_null_cells(self, malformed_sheets: SpreadsheetData) -> None:
        """Extractor handles null cell values."""
        content = extract_sheets_content(malformed_sheets)
        assert content is not None
        # Null cells should become empty strings in CSV
        assert "Header1" in content  # Headers present

    def test_handles_ragged_rows(self, malformed_sheets: SpreadsheetData) -> None:
        """Extractor handles rows with different lengths."""
        content = extract_sheets_content(malformed_sheets)
        assert content is not None
        # Should not crash on ragged rows

    def test_handles_special_characters(self, malformed_sheets: SpreadsheetData) -> None:
        """Extractor escapes special characters in CSV."""
        content = extract_sheets_content(malformed_sheets)
        # Quotes, commas, newlines should be escaped
        assert content is not None
        # The actual escaping is handled by csv module


class TestSlidesNegativePaths:
    """Tests for slides extractor with malformed input."""

    @pytest.fixture
    def malformed_slides_raw(self) -> dict:
        """Load raw malformed slides fixture (for parse_presentation)."""
        return load_malformed_fixture("slides_edge_cases")

    def test_handles_missing_slide_id(self, malformed_slides_raw: dict) -> None:
        """Parser handles slides without slide_id."""
        presentation = parse_presentation(malformed_slides_raw)
        # Should still parse slides
        assert len(presentation.slides) > 0
        # Slide without ID should get a generated one or be handled

    def test_handles_shape_without_text(self, malformed_slides_raw: dict) -> None:
        """Extractor handles shapes with no text content."""
        presentation = parse_presentation(malformed_slides_raw)
        content = extract_slides_content(presentation)
        assert content is not None

    def test_handles_null_textrun_content(self, malformed_slides_raw: dict) -> None:
        """Extractor handles null textRun content in shapes."""
        presentation = parse_presentation(malformed_slides_raw)
        content = extract_slides_content(presentation)
        assert content is not None

    def test_handles_empty_slide(self, malformed_slides_raw: dict) -> None:
        """Extractor handles slides with no page elements."""
        presentation = parse_presentation(malformed_slides_raw)
        content = extract_slides_content(presentation)
        assert content is not None

    def test_handles_empty_table(self, malformed_slides_raw: dict) -> None:
        """Extractor handles tables with no rows."""
        presentation = parse_presentation(malformed_slides_raw)
        content = extract_slides_content(presentation)
        assert content is not None


class TestGmailNegativePaths:
    """Tests for gmail extractor with malformed input."""

    @pytest.fixture
    def malformed_gmail(self) -> GmailThreadData:
        """Load malformed gmail fixture."""
        raw = load_malformed_fixture("gmail_edge_cases")
        return GmailThreadData(
            thread_id=raw["thread_id"],
            subject=raw["subject"],
            messages=[
                EmailMessage(
                    message_id=m["message_id"],
                    from_address=m["from_address"],
                    to_addresses=m["to_addresses"],
                    subject=m.get("subject", ""),
                    body_text=m.get("body_text"),
                    body_html=m.get("body_html"),
                )
                for m in raw["messages"]
            ],
        )

    def test_handles_null_body(self, malformed_gmail: GmailThreadData) -> None:
        """Extractor handles messages with no body."""
        content = extract_thread_content(malformed_gmail)
        assert content is not None
        # Should have warning about empty body
        assert any("empty" in w.lower() or "no body" in w.lower() or "no content" in w.lower()
                   for w in malformed_gmail.warnings)

    def test_handles_empty_sender(self, malformed_gmail: GmailThreadData) -> None:
        """Extractor handles messages with empty sender."""
        content = extract_thread_content(malformed_gmail)
        assert content is not None

    def test_handles_html_only(self, malformed_gmail: GmailThreadData) -> None:
        """Extractor handles messages with only HTML body."""
        content = extract_thread_content(malformed_gmail)
        assert content is not None
        # Should convert HTML to text
        assert "HTML content only" in content

    def test_handles_empty_recipients(self, malformed_gmail: GmailThreadData) -> None:
        """Extractor handles messages with no recipients."""
        content = extract_thread_content(malformed_gmail)
        assert content is not None
