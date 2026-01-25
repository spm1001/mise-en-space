"""
Systematic tests for warning behaviors across all extractors.

Each extractor populates data.warnings with issues encountered during extraction.
These tests verify that warnings are:
1. Generated for expected conditions
2. Properly formatted with useful context
3. Accessible to callers for logging/reporting

Warning patterns by extractor:
- sheets: empty sheets, truncation
- docs: unknown element types, missing inline objects, truncation
- gmail: HTML conversion fallback, empty body, truncation
- slides: missing objectId, thumbnail failures (tested in test_slides.py)
"""

import pytest

from models import (
    SpreadsheetData, SheetTab,
    DocData, DocTab,
    GmailThreadData, EmailMessage,
)
from extractors.sheets import extract_sheets_content
from extractors.docs import extract_doc_content
from extractors.gmail import extract_thread_content, extract_message_content


class TestSheetsWarnings:
    """Warning behaviors for sheets extractor."""

    def test_single_empty_sheet_warning(self) -> None:
        """Single empty sheet generates specific warning."""
        data = SpreadsheetData(
            title="Test",
            spreadsheet_id="test-id",
            sheets=[
                SheetTab(name="Empty Sheet", values=[]),
            ],
        )

        extract_sheets_content(data)

        assert len(data.warnings) == 1
        assert "Sheet 'Empty Sheet' is empty" in data.warnings[0]

    def test_multiple_empty_sheets_warning(self) -> None:
        """Multiple empty sheets generate aggregated warning."""
        data = SpreadsheetData(
            title="Test",
            spreadsheet_id="test-id",
            sheets=[
                SheetTab(name="Sheet1", values=[]),
                SheetTab(name="Sheet2", values=[]),
                SheetTab(name="Sheet3", values=[]),
            ],
        )

        extract_sheets_content(data)

        assert len(data.warnings) == 1
        assert "3 sheets are empty" in data.warnings[0]
        assert "Sheet1" in data.warnings[0]
        assert "Sheet2" in data.warnings[0]
        assert "Sheet3" in data.warnings[0]

    def test_no_warning_for_sheets_with_content(self) -> None:
        """Sheets with content don't generate warnings."""
        data = SpreadsheetData(
            title="Test",
            spreadsheet_id="test-id",
            sheets=[
                SheetTab(name="Data", values=[["A", "B"], ["1", "2"]]),
            ],
        )

        extract_sheets_content(data)

        assert data.warnings == []

    def test_truncation_warning(self) -> None:
        """Truncated content generates warning."""
        data = SpreadsheetData(
            title="Test",
            spreadsheet_id="test-id",
            sheets=[
                SheetTab(name="Big", values=[["x" * 100] * 10 for _ in range(50)]),
            ],
        )

        extract_sheets_content(data, max_length=500)

        assert any("truncated" in w.lower() for w in data.warnings)
        assert any("500" in w for w in data.warnings)

    def test_empty_spreadsheet_no_warning(self) -> None:
        """Completely empty spreadsheet (no sheets) doesn't warn."""
        data = SpreadsheetData(
            title="Empty",
            spreadsheet_id="empty-id",
            sheets=[],
        )

        extract_sheets_content(data)

        # No sheets means nothing to warn about
        assert data.warnings == []


class TestDocsWarnings:
    """Warning behaviors for docs extractor."""

    def test_unknown_element_type_warning(self) -> None:
        """Unknown element types generate warning."""
        data = DocData(
            title="Test",
            document_id="test-id",
            tabs=[
                DocTab(
                    title="Main",
                    tab_id="t.0",
                    index=0,
                    body={
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [
                                        {"unknownElementType": {"data": "mystery"}},
                                        {"anotherUnknown": {}},
                                    ],
                                }
                            }
                        ]
                    },
                )
            ],
        )

        extract_doc_content(data)

        assert len(data.warnings) >= 1
        warning = data.warnings[0]
        assert "Unknown element types" in warning
        assert "unknownElementType" in warning or "anotherUnknown" in warning

    def test_missing_inline_object_warning(self) -> None:
        """Missing inline object reference generates warning."""
        data = DocData(
            title="Test",
            document_id="test-id",
            tabs=[
                DocTab(
                    title="Main",
                    tab_id="t.0",
                    index=0,
                    body={
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [
                                        {"inlineObjectElement": {"inlineObjectId": "kix.missing1"}},
                                        {"inlineObjectElement": {"inlineObjectId": "kix.missing2"}},
                                    ],
                                }
                            }
                        ]
                    },
                    inline_objects={},  # No objects defined
                )
            ],
        )

        extract_doc_content(data)

        assert len(data.warnings) >= 1
        assert any("Missing inline objects" in w for w in data.warnings)
        assert any("kix.missing1" in w or "kix.missing2" in w for w in data.warnings)

    def test_truncation_warning(self) -> None:
        """Truncated content generates warning."""
        long_text = "This is a very long paragraph. " * 100
        data = DocData(
            title="Long Doc",
            document_id="long-id",
            tabs=[
                DocTab(
                    title="Main",
                    tab_id="t.0",
                    index=0,
                    body={
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [
                                        {"textRun": {"content": long_text, "textStyle": {}}}
                                    ],
                                }
                            }
                        ]
                    },
                )
            ],
        )

        extract_doc_content(data, max_length=500)

        assert any("truncated" in w.lower() for w in data.warnings)
        assert any("500" in w for w in data.warnings)

    def test_no_warning_for_normal_doc(self) -> None:
        """Normal document with known elements doesn't warn."""
        data = DocData(
            title="Normal",
            document_id="normal-id",
            tabs=[
                DocTab(
                    title="Main",
                    tab_id="t.0",
                    index=0,
                    body={
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [
                                        {"textRun": {"content": "Hello world\n", "textStyle": {}}}
                                    ],
                                }
                            }
                        ]
                    },
                )
            ],
        )

        extract_doc_content(data)

        assert data.warnings == []


class TestGmailWarnings:
    """Warning behaviors for gmail extractor."""

    def test_empty_body_warning(self) -> None:
        """Message with no body generates warning."""
        msg = EmailMessage(
            message_id="test1",
            from_address="alice@example.com",
            to_addresses=["bob@example.com"],
            body_text=None,
            body_html=None,
        )

        content, warnings = extract_message_content(msg)

        assert content == ""
        assert len(warnings) == 1
        assert "no body content" in warnings[0].lower()

    def test_thread_truncation_warning(self) -> None:
        """Truncated thread generates warning."""
        data = GmailThreadData(
            thread_id="test-thread",
            subject="Long Thread",
            messages=[
                EmailMessage(
                    message_id=f"msg{i}",
                    from_address="alice@example.com",
                    to_addresses=["bob@example.com"],
                    body_text="This is a fairly long message body. " * 50,
                )
                for i in range(10)
            ],
        )

        extract_thread_content(data, max_length=500)

        assert any("truncated" in w.lower() for w in data.warnings)

    def test_message_warning_includes_message_number(self) -> None:
        """Message-level warnings are prefixed with message number."""
        data = GmailThreadData(
            thread_id="test-thread",
            subject="Test",
            messages=[
                EmailMessage(
                    message_id="msg1",
                    from_address="alice@example.com",
                    to_addresses=["bob@example.com"],
                    body_text="Hello",
                ),
                EmailMessage(
                    message_id="msg2",
                    from_address="bob@example.com",
                    to_addresses=["alice@example.com"],
                    body_text=None,
                    body_html=None,
                ),
            ],
        )

        extract_thread_content(data)

        # Second message (index 1, displayed as 2) should have warning
        assert any("Message 2:" in w for w in data.warnings)

    def test_no_warning_for_normal_messages(self) -> None:
        """Normal messages with body don't generate warnings."""
        msg = EmailMessage(
            message_id="test1",
            from_address="alice@example.com",
            to_addresses=["bob@example.com"],
            body_text="Hello, this is a normal message.",
        )

        content, warnings = extract_message_content(msg)

        assert content != ""
        assert warnings == []

    def test_warnings_cleared_on_reextraction(self) -> None:
        """Warnings are cleared when re-extracting same data object."""
        data = GmailThreadData(
            thread_id="test-thread",
            subject="Test",
            messages=[
                EmailMessage(
                    message_id="msg1",
                    from_address="alice@example.com",
                    to_addresses=["bob@example.com"],
                    body_text=None,
                    body_html=None,
                ),
            ],
        )

        # First extraction
        extract_thread_content(data)
        assert len(data.warnings) == 1

        # Second extraction should reset warnings, not accumulate
        extract_thread_content(data)
        assert len(data.warnings) == 1  # Still 1, not 2


class TestWarningsClearedOnReextraction:
    """Verify warnings are properly cleared when re-extracting."""

    def test_sheets_warnings_cleared(self) -> None:
        """Sheets warnings are cleared on re-extraction."""
        data = SpreadsheetData(
            title="Test",
            spreadsheet_id="test-id",
            sheets=[SheetTab(name="Empty", values=[])],
        )

        extract_sheets_content(data)
        extract_sheets_content(data)

        # Should have 1 warning, not 2
        assert len(data.warnings) == 1

    def test_docs_warnings_cleared(self) -> None:
        """Docs warnings are cleared on re-extraction."""
        data = DocData(
            title="Test",
            document_id="test-id",
            tabs=[
                DocTab(
                    title="Main",
                    tab_id="t.0",
                    index=0,
                    body={
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [{"unknownType": {}}],
                                }
                            }
                        ]
                    },
                )
            ],
        )

        extract_doc_content(data)
        extract_doc_content(data)

        # Should have 1 warning, not 2
        assert len(data.warnings) == 1
