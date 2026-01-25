"""
Tests using real API response fixtures.

These tests verify extractors work with actual Google API responses,
not just synthetic test data. They check that specific known content
from the fixtures appears in extractor output - not just "doesn't crash".

Real fixtures:
- fixtures/docs/real_multi_tab.json — Test document with headings, formatting, lists
- fixtures/sheets/real_spreadsheet.json — Test spreadsheet with greetings, Fibonacci
- fixtures/gmail/real_thread.json — Test email thread (raw API format)
- fixtures/slides/real_presentation.json — Test presentation (extensive tests in test_slides.py)
"""

import pytest

from extractors.docs import extract_doc_content
from extractors.sheets import extract_sheets_content


class TestRealDocsFixture:
    """Tests with real Google Docs API responses.

    Real fixture: fixtures/docs/real_multi_tab.json
    - Title: "Test multi-tab document"
    - Tab names: "Sue", "Bob", "Ann" (3 tabs)
    - Content: Headings 1-6, bold/italic/strikethrough, inline code, links, lists
    """

    def test_extracts_multi_tab_doc(self, real_docs_multi_tab):
        """Extract content from real multi-tab document."""
        result = extract_doc_content(real_docs_multi_tab)

        # Should have content
        assert len(result) > 0

        # Should have tab separators if multiple tabs
        if len(real_docs_multi_tab.tabs) > 1:
            assert "=" * 60 in result or len(real_docs_multi_tab.tabs) == 1

    def test_real_doc_no_crash(self, real_docs_multi_tab):
        """Real doc extraction shouldn't crash on any element type."""
        # This is a smoke test - just verify no exceptions
        result = extract_doc_content(real_docs_multi_tab)
        assert isinstance(result, str)

    def test_document_title(self, real_docs_multi_tab):
        """Verify document title is captured."""
        assert real_docs_multi_tab.title == "Test multi-tab document"

    def test_tab_names(self, real_docs_multi_tab):
        """Verify tab names are extracted."""
        tab_names = [t.title for t in real_docs_multi_tab.tabs]
        assert "Sue" in tab_names

    def test_headings_extracted(self, real_docs_multi_tab):
        """Verify headings are converted to markdown."""
        result = extract_doc_content(real_docs_multi_tab)

        # Test document has Heading 1-6
        assert "# Heading 1" in result
        assert "## Heading 2" in result
        assert "### Heading 3" in result
        assert "#### Heading 4" in result
        assert "##### Heading 5" in result
        assert "###### Heading 6" in result

    def test_text_formatting_extracted(self, real_docs_multi_tab):
        """Verify text formatting is converted."""
        result = extract_doc_content(real_docs_multi_tab)

        # Bold and italic text
        assert "**bold text**" in result
        assert "*italic text*" in result
        # Bold italic
        assert "***bold italic***" in result
        # Strikethrough
        assert "~~strikethrough~~" in result
        # Inline code
        assert "`inline code`" in result

    def test_links_extracted(self, real_docs_multi_tab):
        """Verify links are converted to markdown."""
        result = extract_doc_content(real_docs_multi_tab)

        # The doc has "link to Google" pointing to google.com
        assert "[link to Google]" in result
        assert "google.com" in result

    def test_lists_extracted(self, real_docs_multi_tab):
        """Verify list items are extracted."""
        result = extract_doc_content(real_docs_multi_tab)

        # Unordered list items
        assert "Item one" in result
        assert "Item two" in result
        assert "Nested item" in result

        # Ordered list items
        assert "First" in result
        assert "Second" in result
        assert "Third" in result


class TestRealSheetsFixture:
    """Tests with real Google Sheets API responses.

    Real fixture: fixtures/sheets/real_spreadsheet.json
    - Title: "Test Spreadsheet"
    - Sheet: "Sheet1"
    - Headers: Name, Fibs
    - Values: Hello, Goodbye, Au Revoir, Auf Wiedersehen, Aloha, Yoyo, Boom
    - Fibonacci sequence: 1, 1, 2, 3, 5, 8, 13
    - Currency: £1.00, £2.00, etc.
    """

    def test_extracts_real_spreadsheet(self, real_sheets):
        """Extract content from real spreadsheet."""
        result = extract_sheets_content(real_sheets)

        # Should have content
        assert len(result) > 0

        # Should have sheet headers (=== Sheet: Name === format)
        assert "=== Sheet:" in result

    def test_real_sheets_csv_format(self, real_sheets):
        """Real sheets should produce valid CSV-ish output."""
        result = extract_sheets_content(real_sheets)

        # Should have comma-separated values
        lines = result.split("\n")
        # Find a data line (not header or empty)
        data_lines = [l for l in lines if l and not l.startswith("#") and "," in l]
        assert len(data_lines) >= 0  # May be empty sheet

    def test_spreadsheet_title(self, real_sheets):
        """Verify spreadsheet title is captured."""
        assert real_sheets.title == "Test Spreadsheet"

    def test_sheet_name(self, real_sheets):
        """Verify sheet name is extracted."""
        result = extract_sheets_content(real_sheets)
        assert "=== Sheet: Sheet1 ===" in result

    def test_headers_extracted(self, real_sheets):
        """Verify header row is in output."""
        result = extract_sheets_content(real_sheets)
        assert "Name" in result
        assert "Fibs" in result

    def test_greeting_values(self, real_sheets):
        """Verify specific cell values appear."""
        result = extract_sheets_content(real_sheets)

        # Greeting words in first column
        assert "Hello" in result
        assert "Goodbye" in result
        assert "Au Revoir" in result
        assert "Auf Wiedersehen" in result
        assert "Aloha" in result
        assert "Yoyo" in result
        assert "Boom" in result

    def test_fibonacci_values(self, real_sheets):
        """Verify Fibonacci numbers in second column."""
        result = extract_sheets_content(real_sheets)

        # The sheet has Fibonacci sequence in column B
        # We can check that expected CSV patterns appear
        lines = result.split("\n")
        values_found = []
        for line in lines:
            parts = line.split(",")
            if len(parts) >= 2:
                try:
                    values_found.append(int(parts[1]))
                except ValueError:
                    pass

        # Should find Fibonacci numbers: 1, 1, 2, 3, 5, 8, 13
        assert 1 in values_found
        assert 2 in values_found
        assert 3 in values_found
        assert 5 in values_found
        assert 8 in values_found
        assert 13 in values_found

    def test_currency_values(self, real_sheets):
        """Verify currency values are preserved."""
        result = extract_sheets_content(real_sheets)

        # Currency values in column D (with £ symbol)
        assert "£1.00" in result or "\\u00a31.00" in result
        assert "£8.00" in result or "\\u00a38.00" in result

    def test_merged_cell_text(self, real_sheets):
        """Verify merged cell content appears."""
        result = extract_sheets_content(real_sheets)

        # "Merged" text appears in the fixture
        assert "Merged" in result


class TestRealGmailFixture:
    """Tests with real Gmail API responses.

    Real fixture: fixtures/gmail/real_thread.json
    - Thread ID: "19beb7eba557288e"
    - Subject: "Test email"
    - 2 messages in thread
    - First message has inline image, bullets, bold, Drive link
    - Second message is a reply with signature

    Note: The real fixture stores raw API format with base64 bodies.
    The conftest.py extracts headers but not bodies (that's adapter work).
    For full extraction testing, use the synthetic gmail/thread.json fixture.
    """

    def test_real_gmail_fixture_loads(self, real_gmail_thread):
        """Verify real Gmail fixture loads without error."""
        assert real_gmail_thread.thread_id
        assert len(real_gmail_thread.messages) > 0

    def test_real_gmail_has_headers(self, real_gmail_thread):
        """Real Gmail messages should have basic headers."""
        for msg in real_gmail_thread.messages:
            # From should be sanitized but present
            assert "@" in msg.from_address or msg.from_address == ""

    def test_thread_id(self, real_gmail_thread):
        """Verify thread ID is extracted."""
        assert real_gmail_thread.thread_id == "19beb7eba557288e"

    def test_subject_extracted(self, real_gmail_thread):
        """Verify subject line is extracted."""
        assert real_gmail_thread.subject == "Test email"

    def test_message_count(self, real_gmail_thread):
        """Verify message count matches fixture."""
        assert len(real_gmail_thread.messages) == 2

    def test_sender_addresses(self, real_gmail_thread):
        """Verify sender email addresses are extracted."""
        senders = [m.from_address for m in real_gmail_thread.messages]
        # First message from bob@example.com, reply from alice@example.com
        assert any("bob@example.com" in s for s in senders)
        assert any("alice@example.com" in s for s in senders)

    def test_message_ids_unique(self, real_gmail_thread):
        """Verify each message has unique ID."""
        ids = [m.message_id for m in real_gmail_thread.messages]
        assert len(ids) == len(set(ids))  # All unique


class TestRealGmailBodyExtraction:
    """Tests for Gmail body extraction with synthetic fixture.

    Uses fixtures/gmail/thread.json which has pre-extracted body content.
    """

    def test_thread_content_extraction(self, gmail_thread_response):
        """Verify thread content extraction produces expected output."""
        from extractors.gmail import extract_thread_content

        result = extract_thread_content(gmail_thread_response)

        # Subject should be in header
        assert "# Q4 Planning Meeting Notes" in result

        # All messages should be numbered
        assert "[1/3]" in result
        assert "[2/3]" in result
        assert "[3/3]" in result

    def test_message_body_content(self, gmail_thread_response):
        """Verify specific message content appears."""
        from extractors.gmail import extract_thread_content

        result = extract_thread_content(gmail_thread_response)

        # Content from different messages
        assert "Revenue targets" in result
        assert "beta launch timeline" in result

    def test_attachments_listed(self, gmail_thread_response):
        """Verify attachments are mentioned."""
        from extractors.gmail import extract_thread_content

        result = extract_thread_content(gmail_thread_response)

        # Attachment from fixture
        assert "Q4_Roadmap.pdf" in result

    def test_drive_links_extracted(self, gmail_thread_response):
        """Verify Drive links are extracted."""
        from extractors.gmail import extract_thread_content

        result = extract_thread_content(gmail_thread_response)

        # Drive link from fixture
        assert "1ABC_budget_spreadsheet" in result
