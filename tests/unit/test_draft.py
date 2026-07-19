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
    @patch("adapters.gmail.get_sync_client")
    def test_creates_draft_and_returns_result(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.post_json.return_value = {
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
    @patch("adapters.gmail.get_sync_client")
    def test_passes_raw_message_to_api(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.post_json.return_value = {
            "id": "d1", "message": {"id": "m1"},
        }

        create_draft(
            to="bob@example.com",
            subject="Hello",
            body_text="Hi Bob",
            body_html="<p>Hi Bob</p>",
        )

        # Verify the API was called with a body containing raw message
        mock_client.post_json.assert_called_once()
        call_kwargs = mock_client.post_json.call_args[1]
        assert "message" in call_kwargs["json_body"]
        assert "raw" in call_kwargs["json_body"]["message"]


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

    def test_bare_ampersand_escaped_inline_html_passes(self) -> None:
        # New contract (mise-zolowa): agent-authored content is markdown, the
        # draft is user-reviewed, so raw inline HTML passes through; bare
        # ampersands are still entity-escaped.
        result = _content_to_html("Use <b>tags</b> & ampersands")
        assert "<b>tags</b>" in result
        assert "&amp;" in result

    # --- Field report mise-zolowa: GFM must render, not appear literally ---
    def test_gfm_table_renders(self) -> None:
        result = _content_to_html("| A | B |\n|---|---|\n| 1 | 2 |")
        assert "<table>" in result
        assert "<td>1</td>" in result
        assert "|---|" not in result  # the bug: literal pipe-divider row

    def test_bold_renders(self) -> None:
        result = _content_to_html("Some **bold** text")
        assert "<strong>bold</strong>" in result
        assert "**" not in result  # the bug: literal asterisks

    def test_heading_renders(self) -> None:
        result = _content_to_html("# Title\n\nBody")
        assert "<h1>Title</h1>" in result

    def test_list_renders(self) -> None:
        result = _content_to_html("- one\n- two")
        assert "<ul>" in result
        assert "<li>one</li>" in result


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


class TestDraftSignature:
    """Gmail signature grace note — auto-appended to both MIME parts."""

    _SIG = '<div>Sam<br><a href="https://maps.example/q">Our office</a></div>'

    @patch("retry.time.sleep")
    @patch("tools.draft.get_primary_signature")
    @patch("tools.draft.create_draft")
    def test_signature_appended_to_both_parts(
        self, mock_create, mock_sig, _sleep
    ) -> None:
        mock_sig.return_value = self._SIG
        mock_create.return_value = DraftResult(
            draft_id="d1", message_id="m1",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Test",
        )

        result = do_draft(to="alice@example.com", subject="Test", content="Hello")

        kwargs = mock_create.call_args[1]
        # HTML part: raw signature HTML, links intact
        assert self._SIG in kwargs["body_html"]
        assert 'href="https://maps.example/q"' in kwargs["body_html"]
        # Text part: rendered with link as 'text (url)'
        assert "Our office (https://maps.example/q)" in kwargs["body_text"]
        # Cue tells Claude the signature landed
        assert isinstance(result, DoResult)
        assert result.cues["signature"] == "Gmail signature appended automatically"

    @patch("retry.time.sleep")
    @patch("tools.draft.get_file_metadata")
    @patch("tools.draft.get_primary_signature")
    @patch("tools.draft.create_draft")
    def test_signature_lands_after_included_links(
        self, mock_create, mock_sig, mock_meta, _sleep
    ) -> None:
        mock_sig.return_value = self._SIG
        mock_meta.return_value = {
            "name": "Report",
            "mimeType": "application/vnd.google-apps.document",
            "webViewLink": "https://docs.google.com/document/d/xyz/edit",
        }
        mock_create.return_value = DraftResult(
            draft_id="d1", message_id="m1",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Test",
        )

        do_draft(to="alice@example.com", subject="Test",
                 content="See report", include=["xyz"])

        html = mock_create.call_args[1]["body_html"]
        assert html.index("docs.google.com/document/d/xyz") < html.index(self._SIG)

    @patch("retry.time.sleep")
    @patch("tools.draft.get_primary_signature")
    @patch("tools.draft.create_draft")
    def test_fetch_failure_warns_but_draft_succeeds(
        self, mock_create, mock_sig, _sleep
    ) -> None:
        from models import MiseError, ErrorKind
        mock_sig.side_effect = MiseError(ErrorKind.NETWORK_ERROR, "boom")
        mock_create.return_value = DraftResult(
            draft_id="d1", message_id="m1",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Test",
        )

        result = do_draft(to="alice@example.com", subject="Test", content="Hello")

        assert isinstance(result, DoResult)  # draft still created
        assert "signature" not in result.cues
        assert any("boom" in w for w in result.cues["signature_warnings"])
        # Body carries no signature fragments
        assert mock_create.call_args[1]["body_text"] == "Hello"

    @patch("retry.time.sleep")
    @patch("tools.draft.create_draft")
    def test_no_signature_configured_is_silent(self, mock_create, _sleep) -> None:
        # Autouse fixture defaults get_primary_signature to None
        mock_create.return_value = DraftResult(
            draft_id="d1", message_id="m1",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Test",
        )

        result = do_draft(to="alice@example.com", subject="Test", content="Hello")

        assert isinstance(result, DoResult)
        assert "signature" not in result.cues
        assert "signature_warnings" not in result.cues
        assert mock_create.call_args[1]["body_text"] == "Hello"


# =============================================================================
# TOOL: do_draft update mode (file_id= — mise-wemuki)
# =============================================================================


def _existing_draft_headers():
    return {
        "headers": {
            "to": "alice@example.com",
            "cc": "bob@example.com",
            "subject": "Original subject",
            "in-reply-to": "<msg-123@mail.example>",
            "references": "<msg-100@mail.example> <msg-123@mail.example>",
        },
        "thread_id": "thread-789",
    }


class TestDraftUpdate:
    """draft with file_id updates an existing draft in place."""

    def test_update_requires_content(self) -> None:
        result = do_draft(file_id="r123456")
        assert result["error"] is True
        assert "content" in result["message"]

    @patch("tools.draft.get_primary_signature", return_value=None)
    @patch("tools.draft.update_draft")
    @patch("tools.draft.get_draft_headers", return_value=_existing_draft_headers())
    def test_carries_over_unsupplied_fields(
        self, mock_get, mock_update, _sig
    ) -> None:
        mock_update.return_value = DraftResult(
            draft_id="r123456", message_id="m1",
            web_link="https://mail.google.com/mail/#drafts/r123456",
            to="alice@example.com", subject="Original subject",
        )

        result = do_draft(file_id="r123456", content="New body")

        assert isinstance(result, DoResult)
        kwargs = mock_update.call_args.kwargs
        assert kwargs["to"] == "alice@example.com"
        assert kwargs["subject"] == "Original subject"
        assert kwargs["cc"] == "bob@example.com"
        # Threading always carries over — reply drafts stay on their thread
        assert kwargs["thread_id"] == "thread-789"
        assert kwargs["in_reply_to"] == "<msg-123@mail.example>"
        assert kwargs["references"].endswith("<msg-123@mail.example>")
        assert set(result.cues["carried_over"]) == {"to", "subject", "cc"}

    @patch("tools.draft.get_primary_signature", return_value=None)
    @patch("tools.draft.update_draft")
    @patch("tools.draft.get_draft_headers", return_value=_existing_draft_headers())
    def test_resupplied_fields_override(self, mock_get, mock_update, _sig) -> None:
        mock_update.return_value = DraftResult(
            draft_id="r123456", message_id="m1", web_link="w",
            to="alice@example.com", subject="New subject",
        )

        result = do_draft(
            file_id="r123456", content="New body", subject="New subject"
        )

        assert isinstance(result, DoResult)
        kwargs = mock_update.call_args.kwargs
        assert kwargs["subject"] == "New subject"
        assert "subject" not in result.cues.get("carried_over", [])

    @patch("tools.draft.get_draft_headers")
    def test_missing_draft_is_clean_error(self, mock_get) -> None:
        from models import ErrorKind, MiseError

        mock_get.side_effect = MiseError(ErrorKind.NOT_FOUND, "no such draft")
        result = do_draft(file_id="r999", content="x")
        assert result["error"] is True
        assert result["kind"] == "not_found"
        assert "r999" in result["message"]

    @patch("tools.draft.get_primary_signature", return_value=None)
    @patch("tools.draft.get_draft_headers",
           return_value={"headers": {}, "thread_id": None})
    def test_no_recipient_anywhere_errors(self, mock_get, _sig) -> None:
        result = do_draft(file_id="r123", content="x")
        assert result["error"] is True
        assert "to" in result["message"]
