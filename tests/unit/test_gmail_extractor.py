"""
Tests for Gmail extractor.

Tests pure extraction functions with no API calls.
"""

import pytest
from datetime import datetime, timezone

from models import GmailThreadData, EmailMessage, EmailAttachment, ForwardedMessage
from extractors.gmail import (
    extract_thread_content,
    extract_message_content,
    parse_message_payload,
    parse_attachments_from_payload,
    parse_forwarded_messages,
)
from extractors.talon_signature import (
    strip_signature,
    strip_signature_and_quotes,
    strip_quoted_lines,
    split_forward_sections,
)


class TestSignatureStripping:
    """Tests for talon signature stripping."""

    @pytest.mark.parametrize("input_text,expected", [
        # Basic signature with --
        ("Hello!\n\nLet me know.\n\n--\nJohn Doe\njohn@example.com", "Hello!\n\nLet me know."),
        # Thanks signature
        ("Here's the report.\n\nThanks,\nAlice", "Here's the report."),
        # Regards signature
        ("Please review.\n\nRegards,\nBob", "Please review."),
        # Best signature
        ("See attached.\n\nBest,\nCarol", "See attached."),
        # Sent from iPhone
        ("Got it, will do.\n\nSent from my iPhone", "Got it, will do."),
    ])
    def test_strips_signature_variants(self, input_text, expected):
        """Strip various signature patterns."""
        result = strip_signature(input_text)
        assert result == expected

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

    def test_strips_corporate_contact_block(self):
        """Strip URL-dense corporate signatures without explicit markers."""
        text = (
            "Here's the document you asked for.\n\n"
            "Project plan\n"
            "<https://docs.google.com/document/d/abc123>\n\n\n"
            "Alice\n\n"
            "Alice Smith (she/her)\n\n"
            "Innovation Team @ Acme Corp\n"
            "Visit us at 123 Main St\n"
            "<https://goo.gl/maps/abc>\n"
            "Book time at https://calendly.com/alice\n"
            "Learn about our work\n"
            "<https://acme.com/innovation>\n"
            "Phone: +44 1234 567890 <http://+44+1234+567890>\n"
        )
        result = strip_signature_and_quotes(text)
        assert "Here's the document" in result
        assert "Project plan" in result
        assert "Alice Smith" not in result
        assert "Innovation Team" not in result

    def test_strips_corporate_sig_with_phone_and_few_urls(self):
        """Strip corporate signature with phone + fewer than 3 URLs."""
        text = (
            "Please see the attached report.\n\n"
            "Ross Partington\n\n"
            "Head of Ad Sales Research, ITV\n"
            "Phone: +44 20 7157 3000\n"
            "https://www.itv.com\n"
            "https://www.itvmedia.co.uk\n"
        )
        result = strip_signature_and_quotes(text)
        assert "attached report" in result
        assert "Ross Partington" not in result
        assert "Head of Ad Sales" not in result
        assert "itv.com" not in result

    def test_strips_corporate_sig_mobile_label(self):
        """Strip sig where phone uses 'Mobile' or 'Direct' label."""
        text = (
            "Thanks for confirming.\n\n"
            "Jane Doe\n\n"
            "Senior Director, Sales\n"
            "Mobile: 07700 900123\n"
            "https://www.company.com\n"
        )
        result = strip_signature_and_quotes(text)
        assert "confirming" in result
        assert "Jane Doe" not in result

    def test_preserves_content_with_phone_no_name_block(self):
        """Don't strip content mentioning a phone without name-block pattern."""
        text = (
            "Call the office for details.\n"
            "Phone: +44 20 7157 3000\n"
            "See https://example.com for more info.\n"
        )
        result = strip_signature_and_quotes(text)
        assert "Call the office" in result
        assert "Phone:" in result
        assert "example.com" in result

    def test_strips_reply_preamble(self):
        """Strip orphaned 'On ... wrote:' after quote removal."""
        text = (
            "I agree with this approach.\n\n"
            "On Mon, 3 Feb 2026 at 09:15, Bob Jones <bob@example.com> wrote:\n\n"
            "> Original message here"
        )
        result = strip_signature_and_quotes(text)
        assert "I agree" in result
        assert "wrote:" not in result

    def test_preserves_content_with_urls(self):
        """Don't strip content just because it contains URLs."""
        text = (
            "Check out these resources:\n\n"
            "1. https://example.com/doc1\n"
            "2. https://example.com/doc2\n"
            "3. https://example.com/doc3\n\n"
            "Let me know what you think."
        )
        result = strip_signature_and_quotes(text)
        assert "resources" in result
        assert "doc1" in result
        assert "Let me know" in result


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

    @pytest.mark.parametrize("url,expected_id", [
        ("https://docs.google.com/document/d/1ABC123_test/edit", "1ABC123_test"),
        ("https://docs.google.com/spreadsheets/d/1XYZ789/edit", "1XYZ789"),
        ("https://docs.google.com/presentation/d/1SLIDES/edit", "1SLIDES"),
        ("https://drive.google.com/file/d/0BwGZ5_abc123/view", "0BwGZ5_abc123"),
    ])
    def test_extracts_drive_link_variants(self, url, expected_id):
        """Extract Drive links from various URL formats."""
        from extractors.gmail import _extract_drive_links

        links = _extract_drive_links(f"See {url}")
        assert len(links) == 1
        assert links[0]["file_id"] == expected_id

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
        result, warnings = extract_message_content(msg, strip_signature=True)
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
        result, warnings = extract_message_content(msg, strip_signature=False)
        assert "Best," in result  # Signature preserved

    def test_converts_html_to_markdown(self):
        """Convert HTML body to markdown."""
        msg = EmailMessage(
            message_id="test1",
            from_address="alice@example.com",
            to_addresses=["bob@example.com"],
            body_html="<p>Hello <b>Bob</b>!</p><ul><li>Item 1</li><li>Item 2</li></ul>",
        )
        result, warnings = extract_message_content(msg)
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
        result, warnings = extract_message_content(msg)
        assert "Plain text version" in result
        assert "HTML version" not in result

    def test_empty_message(self):
        """Handle message with no body."""
        msg = EmailMessage(
            message_id="test1",
            from_address="alice@example.com",
            to_addresses=["bob@example.com"],
        )
        result, warnings = extract_message_content(msg)
        assert result == ""
        assert "no body content" in warnings[0].lower()  # Should warn about empty body


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


class TestRealFixtureRoundTrip:
    """Round-trip tests: real API fixture → adapter → extractor → content string."""

    def test_extract_message_from_real_fixture(self, real_gmail_thread):
        """Real fixture message → extract_message_content → meaningful content."""
        msg = real_gmail_thread.messages[0]

        result, warnings = extract_message_content(msg)

        # Content decoded from base64 and cleaned
        assert "This is some text" in result
        assert "Bullet 1" in result
        assert len(result) > 20  # Not trivially empty

    def test_extract_reply_from_real_fixture(self, real_gmail_thread):
        """Reply message — new content extracted, quoted original stripped."""
        msg = real_gmail_thread.messages[1]

        result, warnings = extract_message_content(msg, strip_signature=True)

        assert "building on the thread" in result
        # Quoted original thread is stripped (starts with "On Fri, 23 Jan...")
        assert "This is some text" not in result

    def test_extract_thread_from_real_fixture(self, real_gmail_thread):
        """Full thread extraction — subject, both messages, metadata."""
        result = extract_thread_content(real_gmail_thread)

        # Subject header
        assert "Test email" in result

        # Both messages present
        assert "[1/2]" in result
        assert "[2/2]" in result

        # Content from first message
        assert "This is some text" in result

        # Content from reply
        assert "building on the thread" in result

    def test_html_fallback_when_text_preferred(self, real_gmail_thread):
        """When both text and HTML are present, plain text is preferred."""
        msg = real_gmail_thread.messages[0]

        result, _ = extract_message_content(msg)

        # Should use plain text path (no HTML tags in output)
        assert "<div>" not in result
        assert "<ul>" not in result


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


class TestForwardDetection:
    """Tests for forwarded message detection and preservation in talon_signature."""

    def test_gmail_forward_marker_detected(self):
        """Gmail-style forward marker splits body correctly."""
        text = (
            "FYI, see below.\n\n"
            "---------- Forwarded message ---------\n"
            "From: Peter <peter@example.com>\n"
            "Date: Mon, 10 Feb 2026\n"
            "Subject: Analysis\n\n"
            "Here is my analysis of the data."
        )
        own, sections = split_forward_sections(text)
        assert own == "FYI, see below."
        assert len(sections) == 1
        assert "peter@example.com" in sections[0].attribution
        assert "Analysis" in sections[0].attribution
        assert "analysis of the data" in sections[0].body

    def test_apple_forward_marker_detected(self):
        """Apple Mail forward marker splits body correctly."""
        text = (
            "Forwarding this along.\n\n"
            "Begin forwarded message:\n\n"
            "From: Jane <jane@example.com>\n"
            "Subject: Report\n\n"
            "The quarterly numbers are in."
        )
        own, sections = split_forward_sections(text)
        assert own == "Forwarding this along."
        assert len(sections) == 1
        assert "quarterly numbers" in sections[0].body

    def test_no_forward_marker_returns_original(self):
        """Messages without forward markers are returned unchanged."""
        text = "Just a normal reply.\n\nThanks,\nAlice"
        own, sections = split_forward_sections(text)
        assert own == text
        assert sections == []

    def test_forwarded_content_preserved_through_strip_pipeline(self):
        """Forwarded content survives the full strip_signature_and_quotes pipeline."""
        text = (
            "I agree with Peter's analysis.\n\n"
            "> Earlier quoted reply\n"
            "> More quoted text\n\n"
            "Thanks,\nAlice\n\n"
            "---------- Forwarded message ---------\n"
            "From: Peter <peter@example.com>\n"
            "Date: Mon, 10 Feb 2026\n"
            "Subject: VOD Analysis\n\n"
            "The key finding is that VOD numbers are up 15%.\n"
            "See the attached spreadsheet for details."
        )
        result = strip_signature_and_quotes(text)
        # Own content stripped of quotes and signature
        assert "I agree" in result
        assert "> Earlier quoted" not in result
        assert "Thanks," not in result
        # Forwarded content preserved
        assert "--- Forwarded message ---" in result
        assert "VOD numbers are up 15%" in result
        assert "peter@example.com" in result

    def test_quoted_forward_inside_reply_stripped(self):
        """A forward marker inside reply quotes (> ----------) is NOT preserved."""
        text = (
            "My thoughts on this.\n\n"
            "> FYI\n"
            "> ---------- Forwarded message ---------\n"
            "> From: someone@example.com\n"
            "> This was forwarded content in the quoted reply"
        )
        result = strip_signature_and_quotes(text)
        assert "My thoughts" in result
        # The quoted forward is stripped — it's part of the reply chain
        assert "someone@example.com" not in result

    def test_multiple_forwards(self):
        """Multiple forwarded sections are all preserved."""
        text = (
            "See these two messages.\n\n"
            "---------- Forwarded message ---------\n"
            "From: Alice <alice@example.com>\n\n"
            "First forwarded content.\n\n"
            "---------- Forwarded message ---------\n"
            "From: Bob <bob@example.com>\n\n"
            "Second forwarded content."
        )
        own, sections = split_forward_sections(text)
        assert own == "See these two messages."
        assert len(sections) == 2
        assert "First forwarded" in sections[0].body
        assert "Second forwarded" in sections[1].body

    def test_forward_with_no_attribution(self):
        """Forward marker followed directly by content (no From/Date/Subject)."""
        text = (
            "Check this out.\n\n"
            "---------- Forwarded message ---------\n"
            "This content has no attribution headers."
        )
        own, sections = split_forward_sections(text)
        assert len(sections) == 1
        assert sections[0].attribution == ""
        assert "no attribution headers" in sections[0].body

    def test_forward_preserves_quoted_content_after_marker(self):
        """Content with > after a forward marker is forwarded text, not reply quotes."""
        text = (
            "FYI.\n\n"
            "---------- Forwarded message ---------\n"
            "From: Peter <peter@example.com>\n\n"
            "I think the numbers look good.\n"
            "> What do you think about the Q4 targets?\n"
            "I agree with the targets."
        )
        own, sections = split_forward_sections(text)
        # The > line inside the forwarded section is part of the forwarded content
        assert "> What do you think" in sections[0].body


class TestRfc822Extraction:
    """Tests for MIME message/rfc822 parsing and integration."""

    def _make_rfc822_payload(
        self,
        fwd_from: str = "Peter <peter@example.com>",
        fwd_subject: str = "Analysis",
        fwd_body: str = "Key findings here.",
        outer_body: str = "FYI see below",
    ) -> dict:
        """Build a multipart payload with a message/rfc822 part."""
        import base64
        return {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(outer_body.encode()).decode()},
                },
                {
                    "mimeType": "message/rfc822",
                    "filename": "Forwarded message.eml",
                    "body": {"size": 1234},
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "headers": [
                                {"name": "From", "value": fwd_from},
                                {"name": "Subject", "value": fwd_subject},
                                {"name": "Date", "value": "Mon, 10 Feb 2026 09:00:00 +0000"},
                            ],
                            "body": {
                                "data": base64.urlsafe_b64encode(fwd_body.encode()).decode()
                            },
                        }
                    ],
                },
            ],
        }

    def test_rfc822_part_parsed(self):
        """message/rfc822 MIME part is parsed into ForwardedMessage."""
        payload = self._make_rfc822_payload()
        messages = parse_forwarded_messages(payload)
        assert len(messages) == 1
        assert messages[0].from_address == "Peter <peter@example.com>"
        assert messages[0].subject == "Analysis"
        assert "Key findings" in messages[0].body_text

    def test_rfc822_headers_extracted(self):
        """From, Date, Subject extracted from rfc822 part."""
        payload = self._make_rfc822_payload(
            fwd_from="Jane Doe <jane@example.com>",
            fwd_subject="Q4 Report",
        )
        messages = parse_forwarded_messages(payload)
        assert messages[0].from_address == "Jane Doe <jane@example.com>"
        assert messages[0].subject == "Q4 Report"
        assert "2026" in messages[0].date

    def test_no_rfc822_returns_empty(self):
        """Payload without message/rfc822 returns empty list."""
        import base64
        payload = {
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"Just text").decode()},
        }
        messages = parse_forwarded_messages(payload)
        assert messages == []

    def test_rfc822_html_fallback(self):
        """HTML body extracted when no plain text in rfc822 part."""
        import base64
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"Outer").decode()},
                },
                {
                    "mimeType": "message/rfc822",
                    "parts": [
                        {
                            "mimeType": "text/html",
                            "headers": [
                                {"name": "From", "value": "test@example.com"},
                                {"name": "Subject", "value": "HTML only"},
                            ],
                            "body": {
                                "data": base64.urlsafe_b64encode(
                                    b"<p>HTML forwarded content</p>"
                                ).decode()
                            },
                        }
                    ],
                },
            ],
        }
        messages = parse_forwarded_messages(payload)
        assert len(messages) == 1
        assert "HTML forwarded content" in messages[0].body_text

    def test_rfc822_appended_in_extraction(self):
        """MIME-forwarded messages appear in extract_message_content output."""
        msg = EmailMessage(
            message_id="test-fwd",
            from_address="alice@example.com",
            to_addresses=["bob@example.com"],
            body_text="Please see the forwarded analysis below.",
            forwarded_messages=[
                ForwardedMessage(
                    from_address="Peter <peter@example.com>",
                    date="Mon, 10 Feb 2026",
                    subject="VOD Analysis",
                    body_text="VOD numbers are up 15% quarter over quarter.",
                )
            ],
        )
        result, warnings = extract_message_content(msg, strip_signature=True)
        assert "forwarded analysis" in result
        assert "--- Forwarded message ---" in result
        assert "VOD numbers are up 15%" in result
        assert "peter@example.com" in result

    def test_multiple_rfc822_parts(self):
        """Multiple message/rfc822 parts are all extracted."""
        import base64
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"Outer").decode()},
                },
                {
                    "mimeType": "message/rfc822",
                    "parts": [{
                        "mimeType": "text/plain",
                        "headers": [{"name": "From", "value": "a@example.com"}],
                        "body": {"data": base64.urlsafe_b64encode(b"First fwd").decode()},
                    }],
                },
                {
                    "mimeType": "message/rfc822",
                    "parts": [{
                        "mimeType": "text/plain",
                        "headers": [{"name": "From", "value": "b@example.com"}],
                        "body": {"data": base64.urlsafe_b64encode(b"Second fwd").decode()},
                    }],
                },
            ],
        }
        messages = parse_forwarded_messages(payload)
        assert len(messages) == 2
        assert "First fwd" in messages[0].body_text
        assert "Second fwd" in messages[1].body_text

    def test_real_rfc822_fixture_round_trip(self):
        """Real Gmail API fixture with message/rfc822 part round-trips correctly."""
        import json
        from pathlib import Path

        fixture_path = Path(__file__).parent.parent.parent / "fixtures" / "gmail" / "real_rfc822_forward.json"
        if not fixture_path.exists():
            pytest.skip("Real rfc822 fixture not available")

        with open(fixture_path) as f:
            thread = json.load(f)

        msg = thread["messages"][0]
        forwards = parse_forwarded_messages(msg["payload"])

        assert len(forwards) == 1
        fwd = forwards[0]
        assert fwd.subject == "RE: Clock Number Questions"
        assert fwd.from_address  # has a sender
        assert len(fwd.body_text) > 100  # substantial content
        assert fwd.body_text.startswith("Hi ")  # real email opening
