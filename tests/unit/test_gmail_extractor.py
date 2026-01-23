"""
Tests for Gmail extractor.

Tests pure extraction functions with no API calls.
"""

import pytest
from datetime import datetime, timezone

from models import GmailThreadData, EmailMessage, EmailAttachment
from extractors.gmail import (
    extract_thread_content,
    extract_message_content,
    parse_message_payload,
    parse_attachments_from_payload,
)
from extractors.talon_signature import (
    strip_signature,
    strip_signature_and_quotes,
    strip_quoted_lines,
)


class TestSignatureStripping:
    """Tests for talon signature stripping."""

    def test_strips_basic_signature(self):
        """Strip common signature patterns."""
        text = "Hello!\n\nLet me know.\n\n--\nJohn Doe\njohn@example.com"
        result = strip_signature(text)
        assert result == "Hello!\n\nLet me know."

    def test_strips_thanks_signature(self):
        """Strip 'Thanks' signature."""
        text = "Here's the report.\n\nThanks,\nAlice"
        result = strip_signature(text)
        assert result == "Here's the report."

    def test_strips_regards_signature(self):
        """Strip 'Regards' signature."""
        text = "Please review.\n\nRegards,\nBob"
        result = strip_signature(text)
        assert result == "Please review."

    def test_strips_best_signature(self):
        """Strip 'Best' signature."""
        text = "See attached.\n\nBest,\nCarol"
        result = strip_signature(text)
        assert result == "See attached."

    def test_strips_phone_signature(self):
        """Strip 'Sent from my iPhone' signature."""
        text = "Got it, will do.\n\nSent from my iPhone"
        result = strip_signature(text)
        assert result == "Got it, will do."

    def test_strips_quoted_lines(self):
        """Strip lines starting with >."""
        text = "My reply.\n\n> Original message\n> More original\n\nMy follow-up."
        result = strip_quoted_lines(text)
        assert "> Original" not in result
        assert "My reply." in result
        assert "My follow-up." in result

    def test_combined_signature_and_quotes(self):
        """Strip both signatures and quotes."""
        text = "I agree.\n\n> Previous discussion here\n> More quotes\n\nThanks,\nJohn"
        result = strip_signature_and_quotes(text)
        assert result == "I agree."

    def test_preserves_short_messages(self):
        """Don't strip content from very short messages."""
        text = "OK!"
        result = strip_signature(text)
        assert result == "OK!"

    def test_preserves_dashes_in_content(self):
        """Don't strip list items that look like signature markers."""
        text = "Options:\n- Option A\n- Option B\n\nPick one."
        result = strip_signature(text)
        assert "Option A" in result
        assert "Option B" in result


class TestHTMLCleaning:
    """Tests for HTML cleaning before markdown conversion."""

    def test_removes_tracking_pixels(self):
        """Remove 1x1 tracking images."""
        from extractors.gmail import _clean_html_for_conversion

        html = '<p>Content</p><img width="1" height="1" src="track.gif"><p>More</p>'
        result = _clean_html_for_conversion(html)
        assert 'width="1"' not in result
        assert "Content" in result

    def test_removes_mso_conditionals(self):
        """Remove Outlook-specific blocks."""
        from extractors.gmail import _clean_html_for_conversion

        html = '<p>Content</p><!--[if mso]>Outlook stuff<![endif]--><p>More</p>'
        result = _clean_html_for_conversion(html)
        assert "mso" not in result
        assert "Outlook" not in result

    def test_removes_hidden_elements(self):
        """Remove display:none elements."""
        from extractors.gmail import _clean_html_for_conversion

        html = '<p>Visible</p><span style="display:none">Hidden</span><p>More</p>'
        result = _clean_html_for_conversion(html)
        assert "Hidden" not in result
        assert "Visible" in result


class TestDriveLinkExtraction:
    """Tests for extracting Google Drive links from text."""

    def test_extracts_docs_link(self):
        """Extract Google Docs link."""
        from extractors.gmail import _extract_drive_links

        text = "See https://docs.google.com/document/d/1ABC123_test/edit"
        links = _extract_drive_links(text)
        assert len(links) == 1
        assert links[0]["file_id"] == "1ABC123_test"

    def test_extracts_sheets_link(self):
        """Extract Google Sheets link."""
        from extractors.gmail import _extract_drive_links

        text = "Budget: https://docs.google.com/spreadsheets/d/1XYZ789/edit"
        links = _extract_drive_links(text)
        assert len(links) == 1
        assert links[0]["file_id"] == "1XYZ789"

    def test_extracts_drive_file_link(self):
        """Extract Drive file link."""
        from extractors.gmail import _extract_drive_links

        text = "File: https://drive.google.com/file/d/0BwGZ5_abc123/view"
        links = _extract_drive_links(text)
        assert len(links) == 1
        assert links[0]["file_id"] == "0BwGZ5_abc123"

    def test_extracts_multiple_links(self):
        """Extract multiple Drive links."""
        from extractors.gmail import _extract_drive_links

        text = """
        Doc: https://docs.google.com/document/d/doc123/edit
        Sheet: https://docs.google.com/spreadsheets/d/sheet456/edit
        """
        links = _extract_drive_links(text)
        assert len(links) == 2
        file_ids = {l["file_id"] for l in links}
        assert "doc123" in file_ids
        assert "sheet456" in file_ids

    def test_deduplicates_links(self):
        """Don't return same link twice."""
        from extractors.gmail import _extract_drive_links

        text = """
        First mention: https://docs.google.com/document/d/same123/edit
        Second mention: https://docs.google.com/document/d/same123/view
        """
        links = _extract_drive_links(text)
        assert len(links) == 1


class TestMessageExtraction:
    """Tests for extracting content from EmailMessage."""

    def test_extracts_plain_text(self):
        """Extract plain text body."""
        msg = EmailMessage(
            message_id="test1",
            from_address="alice@example.com",
            to_addresses=["bob@example.com"],
            body_text="Hello Bob!\n\nHow are you?\n\nBest,\nAlice",
        )
        result = extract_message_content(msg, strip_signature=True)
        assert "Hello Bob!" in result
        assert "How are you?" in result
        assert "Best," not in result  # Signature stripped

    def test_extracts_plain_text_no_strip(self):
        """Extract plain text without stripping signature."""
        msg = EmailMessage(
            message_id="test1",
            from_address="alice@example.com",
            to_addresses=["bob@example.com"],
            body_text="Hello!\n\nBest,\nAlice",
        )
        result = extract_message_content(msg, strip_signature=False)
        assert "Best," in result  # Signature preserved

    def test_converts_html_to_markdown(self):
        """Convert HTML body to markdown."""
        msg = EmailMessage(
            message_id="test1",
            from_address="alice@example.com",
            to_addresses=["bob@example.com"],
            body_html="<p>Hello <b>Bob</b>!</p><ul><li>Item 1</li><li>Item 2</li></ul>",
        )
        result = extract_message_content(msg)
        # Should contain converted content (exact format depends on markitdown)
        assert "Bob" in result
        assert "Item 1" in result

    def test_prefers_plain_text_over_html(self):
        """Prefer plain text when both are available."""
        msg = EmailMessage(
            message_id="test1",
            from_address="alice@example.com",
            to_addresses=["bob@example.com"],
            body_text="Plain text version",
            body_html="<p>HTML version</p>",
        )
        result = extract_message_content(msg)
        assert "Plain text version" in result
        assert "HTML version" not in result

    def test_empty_message(self):
        """Handle message with no body."""
        msg = EmailMessage(
            message_id="test1",
            from_address="alice@example.com",
            to_addresses=["bob@example.com"],
        )
        result = extract_message_content(msg)
        assert result == ""


class TestThreadExtraction:
    """Tests for extracting full thread content."""

    def test_extracts_thread(self, gmail_thread_response):
        """Extract content from full thread."""
        result = extract_thread_content(gmail_thread_response)

        # Should have subject as header
        assert "# Q4 Planning Meeting Notes" in result

        # Should have all three messages
        assert "[1/3]" in result
        assert "[2/3]" in result
        assert "[3/3]" in result

        # Should have sender info
        assert "alice@example.com" in result
        assert "bob@example.com" in result
        assert "carol@example.com" in result

        # Should have content from messages
        assert "Revenue targets" in result
        assert "beta launch timeline" in result

        # Should strip signatures
        assert "Sent from my iPhone" not in result
        assert "Alice Smith\nHead of Product" not in result

        # Should strip quotes
        assert "> Thanks Alice!" not in result

    def test_includes_attachments_summary(self, gmail_thread_response):
        """Include attachment info in output."""
        result = extract_thread_content(gmail_thread_response)
        assert "Q4_Roadmap.pdf" in result
        assert "240" in result or "KB" in result  # Size indication

    def test_includes_drive_links_summary(self, gmail_thread_response):
        """Include Drive links in output."""
        result = extract_thread_content(gmail_thread_response)
        assert "1ABC_budget_spreadsheet" in result

    def test_respects_max_length(self, gmail_thread_response):
        """Truncate when max_length exceeded."""
        result = extract_thread_content(gmail_thread_response, max_length=500)
        assert len(result) <= 600  # Some slack for truncation message
        assert "TRUNCATED" in result

    def test_no_strip_signatures_option(self, gmail_thread_response):
        """Can disable signature stripping."""
        result = extract_thread_content(gmail_thread_response, strip_signatures=False)
        # Should still have content but may include signatures
        assert "Q4 Planning Meeting Notes" in result


class TestPayloadParsing:
    """Tests for Gmail API payload parsing utilities."""

    def test_parses_simple_text_payload(self):
        """Parse simple text/plain message."""
        import base64
        content = "Hello world!"
        encoded = base64.urlsafe_b64encode(content.encode()).decode()

        payload = {
            "mimeType": "text/plain",
            "body": {"data": encoded}
        }

        plain, html = parse_message_payload(payload)
        assert plain == "Hello world!"
        assert html is None

    def test_parses_simple_html_payload(self):
        """Parse simple text/html message."""
        import base64
        content = "<p>Hello world!</p>"
        encoded = base64.urlsafe_b64encode(content.encode()).decode()

        payload = {
            "mimeType": "text/html",
            "body": {"data": encoded}
        }

        plain, html = parse_message_payload(payload)
        assert plain is None
        assert html == "<p>Hello world!</p>"

    def test_parses_multipart_payload(self):
        """Parse multipart message with both text and HTML."""
        import base64
        text_content = "Plain text"
        html_content = "<p>HTML</p>"

        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(text_content.encode()).decode()}
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": base64.urlsafe_b64encode(html_content.encode()).decode()}
                }
            ]
        }

        plain, html = parse_message_payload(payload)
        assert plain == "Plain text"
        assert html == "<p>HTML</p>"

    def test_parses_attachments(self):
        """Parse attachment metadata from payload."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": "SGVsbG8="}
                },
                {
                    "filename": "report.pdf",
                    "mimeType": "application/pdf",
                    "body": {
                        "attachmentId": "ANGjdJ_test123",
                        "size": 12345
                    }
                }
            ]
        }

        attachments = parse_attachments_from_payload(payload)
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "report.pdf"
        assert attachments[0]["mimeType"] == "application/pdf"
        assert attachments[0]["size"] == 12345
        assert attachments[0]["attachment_id"] == "ANGjdJ_test123"
