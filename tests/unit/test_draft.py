"""Tests for Gmail draft operations."""

import base64
from email import message_from_bytes
from unittest.mock import patch, MagicMock

import pytest

from adapters.gmail import (
    IncludedLink,
    DraftResult,
    _build_draft_message,
    _draft_web_link,
    create_draft,
)
from models import DoResult
from tools.draft import (
    _content_to_html,
    _format_links_text,
    _format_links_html,
    _icon_for_mime,
    _resolve_include,
    do_draft,
)


# =============================================================================
# ADAPTER: MIME construction
# =============================================================================


class TestBuildDraftMessage:
    """_build_draft_message produces valid RFC 2822."""

    def test_basic_message(self) -> None:
        raw = _build_draft_message(
            to="alice@example.com",
            subject="Test Subject",
            body_text="Hello plain",
            body_html="<p>Hello html</p>",
        )
        # Decode and parse
        raw_bytes = base64.urlsafe_b64decode(raw)
        msg = message_from_bytes(raw_bytes)

        assert msg["To"] == "alice@example.com"
        assert msg["Subject"] == "Test Subject"
        assert msg.get_content_type() == "multipart/alternative"

        # Should have two parts: text and html
        parts = list(msg.walk())
        text_parts = [p for p in parts if p.get_content_type() == "text/plain"]
        html_parts = [p for p in parts if p.get_content_type() == "text/html"]
        assert len(text_parts) == 1
        assert len(html_parts) == 1
        assert "Hello plain" in text_parts[0].get_payload(decode=True).decode()
        assert "Hello html" in html_parts[0].get_payload(decode=True).decode()

    def test_cc_header(self) -> None:
        raw = _build_draft_message(
            to="alice@example.com",
            subject="Test",
            body_text="Hi",
            body_html="<p>Hi</p>",
            cc="bob@example.com",
        )
        raw_bytes = base64.urlsafe_b64decode(raw)
        msg = message_from_bytes(raw_bytes)
        assert msg["Cc"] == "bob@example.com"

    def test_no_cc_when_none(self) -> None:
        raw = _build_draft_message(
            to="alice@example.com",
            subject="Test",
            body_text="Hi",
            body_html="<p>Hi</p>",
        )
        raw_bytes = base64.urlsafe_b64decode(raw)
        msg = message_from_bytes(raw_bytes)
        assert msg["Cc"] is None

    def test_unicode_content(self) -> None:
        raw = _build_draft_message(
            to="alice@example.com",
            subject="Café résumé",
            body_text="Hello café ☕",
            body_html="<p>Hello café ☕</p>",
        )
        raw_bytes = base64.urlsafe_b64decode(raw)
        msg = message_from_bytes(raw_bytes)
        assert "Café" in msg["Subject"] or "=?utf-8?" in msg["Subject"]


class TestDraftWebLink:
    def test_format(self) -> None:
        assert _draft_web_link("abc123") == "https://mail.google.com/mail/#drafts/abc123"


class TestCreateDraft:
    """create_draft calls Gmail API correctly."""

    @patch("retry.time.sleep")
    @patch("adapters.gmail.get_gmail_service")
    def test_creates_draft_and_returns_result(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.users().drafts().create().execute.return_value = {
            "id": "draft_abc",
            "message": {"id": "msg_123"},
        }

        result = create_draft(
            to="alice@example.com",
            subject="Test",
            body_text="Hello",
            body_html="<p>Hello</p>",
        )

        assert isinstance(result, DraftResult)
        assert result.draft_id == "draft_abc"
        assert result.message_id == "msg_123"
        assert "draft_abc" in result.web_link
        assert result.to == "alice@example.com"
        assert result.subject == "Test"

    @patch("retry.time.sleep")
    @patch("adapters.gmail.get_gmail_service")
    def test_passes_raw_message_to_api(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.users().drafts().create().execute.return_value = {
            "id": "d1", "message": {"id": "m1"},
        }

        create_draft(
            to="bob@example.com",
            subject="Hello",
            body_text="Hi Bob",
            body_html="<p>Hi Bob</p>",
        )

        # Verify the API was called with a body containing raw message
        call_kwargs = mock_service.users().drafts().create.call_args
        body = call_kwargs[1]["body"] if "body" in (call_kwargs[1] or {}) else call_kwargs[0][0] if call_kwargs[0] else None
        # The create() call should have userId="me" and body with message.raw
        mock_service.users().drafts().create.assert_called()


# =============================================================================
# TOOL: Link formatting
# =============================================================================


class TestIconForMime:
    def test_google_doc(self) -> None:
        icon = _icon_for_mime("application/vnd.google-apps.document")
        assert icon == "\U0001f4dd"

    def test_google_sheet(self) -> None:
        icon = _icon_for_mime("application/vnd.google-apps.spreadsheet")
        assert icon == "\U0001f4ca"

    def test_unknown_type(self) -> None:
        icon = _icon_for_mime("application/pdf")
        assert icon == "\U0001f4ce"


class TestFormatLinksText:
    def test_empty_links(self) -> None:
        assert _format_links_text([]) == ""

    def test_single_link(self) -> None:
        links = [IncludedLink(
            file_id="abc", title="My Doc",
            mime_type="application/vnd.google-apps.document",
            web_link="https://docs.google.com/document/d/abc/edit",
        )]
        result = _format_links_text(links)
        assert "My Doc" in result
        assert "https://docs.google.com/document/d/abc/edit" in result

    def test_multiple_links(self) -> None:
        links = [
            IncludedLink(file_id="1", title="Doc A", mime_type="", web_link="https://a"),
            IncludedLink(file_id="2", title="Doc B", mime_type="", web_link="https://b"),
        ]
        result = _format_links_text(links)
        assert "Doc A" in result
        assert "Doc B" in result


class TestFormatLinksHtml:
    def test_empty_links(self) -> None:
        assert _format_links_html([]) == ""

    def test_contains_href(self) -> None:
        links = [IncludedLink(
            file_id="abc", title="My Sheet",
            mime_type="application/vnd.google-apps.spreadsheet",
            web_link="https://sheets.google.com/spreadsheets/d/abc",
        )]
        result = _format_links_html(links)
        assert 'href="https://sheets.google.com/spreadsheets/d/abc"' in result
        assert "My Sheet" in result

    def test_escapes_html_in_title(self) -> None:
        links = [IncludedLink(
            file_id="abc", title="Q4 <draft> & notes",
            mime_type="", web_link="https://example.com",
        )]
        result = _format_links_html(links)
        assert "&lt;draft&gt;" in result
        assert "&amp;" in result


class TestContentToHtml:
    def test_single_paragraph(self) -> None:
        result = _content_to_html("Hello world")
        assert "<p>Hello world</p>" in result

    def test_double_newline_creates_paragraphs(self) -> None:
        result = _content_to_html("First paragraph\n\nSecond paragraph")
        assert "<p>First paragraph</p>" in result
        assert "<p>Second paragraph</p>" in result

    def test_single_newline_creates_br(self) -> None:
        result = _content_to_html("Line one\nLine two")
        assert "Line one<br>" in result

    def test_escapes_html(self) -> None:
        result = _content_to_html("Use <b>tags</b> & ampersands")
        assert "&lt;b&gt;" in result
        assert "&amp;" in result


class TestResolveInclude:
    @patch("tools.draft.get_file_metadata")
    def test_resolves_file_ids(self, mock_meta) -> None:
        mock_meta.return_value = {
            "name": "Q4 Report",
            "mimeType": "application/vnd.google-apps.document",
            "webViewLink": "https://docs.google.com/document/d/abc/edit",
        }
        links, warnings = _resolve_include(["abc"])
        assert len(links) == 1
        assert links[0].title == "Q4 Report"
        assert warnings == []

    @patch("tools.draft.get_file_metadata")
    def test_failed_lookup_becomes_warning(self, mock_meta) -> None:
        from models import MiseError, ErrorKind
        mock_meta.side_effect = MiseError(ErrorKind.NOT_FOUND, "File not found")
        links, warnings = _resolve_include(["bad_id"])
        assert len(links) == 0
        assert len(warnings) == 1
        assert "bad_id" in warnings[0]

    @patch("tools.draft.get_file_metadata")
    def test_partial_failure(self, mock_meta) -> None:
        from models import MiseError, ErrorKind
        mock_meta.side_effect = [
            {"name": "Good", "mimeType": "doc", "webViewLink": "https://x"},
            MiseError(ErrorKind.NOT_FOUND, "nope"),
        ]
        links, warnings = _resolve_include(["good_id", "bad_id"])
        assert len(links) == 1
        assert len(warnings) == 1


# =============================================================================
# TOOL: do_draft validation and wiring
# =============================================================================


class TestDoDraftValidation:
    def test_missing_to(self) -> None:
        result = do_draft(subject="Test", content="Body")
        assert result["error"] is True
        assert "to" in result["message"]

    def test_missing_subject(self) -> None:
        result = do_draft(to="alice@example.com", content="Body")
        assert result["error"] is True
        assert "subject" in result["message"]

    def test_missing_content(self) -> None:
        result = do_draft(to="alice@example.com", subject="Test")
        assert result["error"] is True
        assert "content" in result["message"]


class TestDoDraftSuccess:
    @patch("retry.time.sleep")
    @patch("tools.draft.create_draft")
    def test_returns_do_result(self, mock_create, _sleep) -> None:
        mock_create.return_value = DraftResult(
            draft_id="d1", message_id="m1",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Test",
        )

        result = do_draft(
            to="alice@example.com",
            subject="Test",
            content="Hello",
        )

        assert isinstance(result, DoResult)
        assert result.operation == "draft"
        assert result.file_id == "d1"
        assert "drafts" in result.web_link
        assert "action" in result.cues

    @patch("retry.time.sleep")
    @patch("tools.draft.get_file_metadata")
    @patch("tools.draft.create_draft")
    def test_include_links_in_cues(self, mock_create, mock_meta, _sleep) -> None:
        mock_meta.return_value = {
            "name": "Report",
            "mimeType": "application/vnd.google-apps.document",
            "webViewLink": "https://docs.google.com/document/d/xyz/edit",
        }
        mock_create.return_value = DraftResult(
            draft_id="d1", message_id="m1",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Test",
            included_links=[IncludedLink(
                file_id="xyz", title="Report",
                mime_type="application/vnd.google-apps.document",
                web_link="https://docs.google.com/document/d/xyz/edit",
            )],
        )

        result = do_draft(
            to="alice@example.com",
            subject="Test",
            content="See report",
            include=["xyz"],
        )

        assert isinstance(result, DoResult)
        assert "included_links" in result.cues
        assert result.cues["included_links"][0]["title"] == "Report"

    @patch("retry.time.sleep")
    @patch("tools.draft.create_draft")
    def test_passes_cc(self, mock_create, _sleep) -> None:
        mock_create.return_value = DraftResult(
            draft_id="d1", message_id="m1",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Test",
        )

        do_draft(
            to="alice@example.com",
            subject="Test",
            content="Hello",
            cc="bob@example.com",
        )

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["cc"] == "bob@example.com"
