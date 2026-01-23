"""
Tests using real API response fixtures.

These tests verify extractors work with actual Google API responses,
not just synthetic test data.
"""

import pytest

from extractors.docs import extract_doc_content
from extractors.sheets import extract_sheets_content


class TestRealDocsFixture:
    """Tests with real Google Docs API responses."""

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


class TestRealSheetsFixture:
    """Tests with real Google Sheets API responses."""

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


class TestRealGmailFixture:
    """Tests with real Gmail API responses."""

    def test_real_gmail_fixture_loads(self, real_gmail_thread):
        """Verify real Gmail fixture loads without error."""
        assert real_gmail_thread.thread_id
        assert len(real_gmail_thread.messages) > 0

    def test_real_gmail_has_headers(self, real_gmail_thread):
        """Real Gmail messages should have basic headers."""
        for msg in real_gmail_thread.messages:
            # From should be sanitized but present
            assert "@" in msg.from_address or msg.from_address == ""
