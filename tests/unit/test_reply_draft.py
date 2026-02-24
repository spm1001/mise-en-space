"""Tests for Gmail reply draft operations."""

import base64
from datetime import datetime, timezone
from email import message_from_bytes
from unittest.mock import patch, MagicMock

import pytest

from adapters.gmail import (
    IncludedLink,
    ReplyDraftResult,
    _build_draft_message,
    _build_references,
    _ensure_re_prefix,
    create_reply_draft,
)
from models import DoResult, EmailMessage, GmailThreadData
from tools.reply_draft import (
    _extract_email,
    _infer_recipients,
    _infer_recipients_all,
    do_reply_draft,
)


# =============================================================================
# FIXTURES
# =============================================================================


def _make_message(
    message_id: str = "msg_001",
    from_address: str = "alice@example.com",
    to_addresses: list[str] | None = None,
    cc_addresses: list[str] | None = None,
    subject: str = "Original Subject",
    message_id_header: str | None = "<abc123@mail.example.com>",
    in_reply_to: str | None = None,
    references: str | None = None,
) -> EmailMessage:
    """Build an EmailMessage for testing."""
    return EmailMessage(
        message_id=message_id,
        from_address=from_address,
        to_addresses=to_addresses or ["me@example.com"],
        cc_addresses=cc_addresses or [],
        subject=subject,
        date=datetime(2026, 2, 24, tzinfo=timezone.utc),
        body_text="Hello",
        message_id_header=message_id_header,
        in_reply_to=in_reply_to,
        references=references,
    )


def _make_thread(
    thread_id: str = "thread_abc",
    subject: str = "Original Subject",
    messages: list[EmailMessage] | None = None,
) -> GmailThreadData:
    """Build a GmailThreadData for testing."""
    msgs = messages or [_make_message()]
    return GmailThreadData(
        thread_id=thread_id,
        subject=subject,
        messages=msgs,
    )


# =============================================================================
# ADAPTER: Threading helpers
# =============================================================================


class TestBuildReferences:
    """_build_references constructs correct threading headers."""

    def test_basic_reply(self) -> None:
        msg = _make_message(message_id_header="<abc@example.com>")
        in_reply_to, references = _build_references(msg)
        assert in_reply_to == "<abc@example.com>"
        assert references == "<abc@example.com>"

    def test_continued_thread(self) -> None:
        msg = _make_message(
            message_id_header="<def@example.com>",
            references="<abc@example.com>",
        )
        in_reply_to, references = _build_references(msg)
        assert in_reply_to == "<def@example.com>"
        assert references == "<abc@example.com> <def@example.com>"

    def test_long_references_chain(self) -> None:
        existing = "<a@x.com> <b@x.com> <c@x.com>"
        msg = _make_message(
            message_id_header="<d@x.com>",
            references=existing,
        )
        in_reply_to, references = _build_references(msg)
        assert in_reply_to == "<d@x.com>"
        assert references == f"{existing} <d@x.com>"

    def test_no_message_id_returns_none(self) -> None:
        msg = _make_message(message_id_header=None)
        in_reply_to, references = _build_references(msg)
        assert in_reply_to is None
        assert references is None


class TestEnsureRePrefix:
    def test_adds_prefix(self) -> None:
        assert _ensure_re_prefix("Original Subject") == "Re: Original Subject"

    def test_preserves_existing_prefix(self) -> None:
        assert _ensure_re_prefix("Re: Already there") == "Re: Already there"

    def test_case_insensitive(self) -> None:
        assert _ensure_re_prefix("re: lowercase") == "re: lowercase"
        assert _ensure_re_prefix("RE: uppercase") == "RE: uppercase"


class TestBuildDraftMessageWithThreading:
    """_build_draft_message handles threading headers."""

    def test_threading_headers_present(self) -> None:
        raw = _build_draft_message(
            to="bob@example.com",
            subject="Re: Test",
            body_text="Reply text",
            body_html="<p>Reply text</p>",
            in_reply_to="<abc@example.com>",
            references="<abc@example.com>",
        )
        raw_bytes = base64.urlsafe_b64decode(raw)
        msg = message_from_bytes(raw_bytes)
        assert msg["In-Reply-To"] == "<abc@example.com>"
        assert msg["References"] == "<abc@example.com>"

    def test_no_threading_headers_when_none(self) -> None:
        raw = _build_draft_message(
            to="bob@example.com",
            subject="Test",
            body_text="Hello",
            body_html="<p>Hello</p>",
        )
        raw_bytes = base64.urlsafe_b64decode(raw)
        msg = message_from_bytes(raw_bytes)
        assert msg["In-Reply-To"] is None
        assert msg["References"] is None


class TestCreateReplyDraft:
    """create_reply_draft calls Gmail API with threadId."""

    @patch("retry.time.sleep")
    @patch("adapters.gmail.get_gmail_service")
    def test_passes_thread_id_to_api(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.users().drafts().create().execute.return_value = {
            "id": "draft_reply",
            "message": {"id": "msg_reply"},
        }

        result = create_reply_draft(
            thread_id="thread_abc",
            to="alice@example.com",
            subject="Re: Test",
            body_text="Reply",
            body_html="<p>Reply</p>",
            in_reply_to="<abc@example.com>",
            references="<abc@example.com>",
        )

        assert isinstance(result, ReplyDraftResult)
        assert result.draft_id == "draft_reply"
        assert result.thread_id == "thread_abc"
        assert "draft_reply" in result.web_link

        # Verify threadId was in the API call body
        create_call = mock_service.users().drafts().create
        create_call.assert_called()

    @patch("retry.time.sleep")
    @patch("adapters.gmail.get_gmail_service")
    def test_returns_correct_result_shape(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.users().drafts().create().execute.return_value = {
            "id": "d1", "message": {"id": "m1"},
        }

        result = create_reply_draft(
            thread_id="t1",
            to="bob@example.com",
            subject="Re: Hello",
            body_text="Hi",
            body_html="<p>Hi</p>",
            cc="carol@example.com",
        )

        assert result.to == "bob@example.com"
        assert result.subject == "Re: Hello"
        assert result.cc == "carol@example.com"


# =============================================================================
# TOOL: Recipient inference
# =============================================================================


class TestExtractEmail:
    def test_bare_email(self) -> None:
        assert _extract_email("alice@example.com") == "alice@example.com"

    def test_display_name_format(self) -> None:
        assert _extract_email("Alice Smith <alice@example.com>") == "alice@example.com"

    def test_normalizes_case(self) -> None:
        assert _extract_email("Alice@Example.COM") == "alice@example.com"


class TestInferRecipients:
    def test_simple_reply_to_sender(self) -> None:
        msg = _make_message(from_address="alice@example.com")
        to, cc = _infer_recipients(msg)
        assert to == "alice@example.com"
        assert cc is None


class TestInferRecipientsAll:
    def test_reply_all_to_sender_and_others(self) -> None:
        msg = _make_message(
            from_address="alice@example.com",
            to_addresses=["me@example.com", "bob@example.com"],
            cc_addresses=["carol@example.com"],
        )
        to, cc = _infer_recipients_all(msg, authenticated_email="me@example.com")
        assert to == "alice@example.com"
        assert cc is not None
        assert "bob@example.com" in cc
        assert "carol@example.com" in cc
        assert "me@example.com" not in cc

    def test_reply_all_excludes_sender_from_cc(self) -> None:
        msg = _make_message(
            from_address="Alice <alice@example.com>",
            to_addresses=["alice@example.com", "bob@example.com"],
        )
        to, cc = _infer_recipients_all(msg)
        assert to == "Alice <alice@example.com>"
        # alice should be excluded from Cc (she's the sender)
        assert cc is not None
        assert "alice@example.com" not in cc.lower()
        assert "bob@example.com" in cc

    def test_reply_all_no_others(self) -> None:
        msg = _make_message(
            from_address="alice@example.com",
            to_addresses=["me@example.com"],
        )
        to, cc = _infer_recipients_all(msg, authenticated_email="me@example.com")
        assert to == "alice@example.com"
        assert cc is None  # No other recipients to Cc

    def test_reply_all_deduplicates_sender(self) -> None:
        msg = _make_message(
            from_address="alice@example.com",
            to_addresses=["me@example.com"],
            cc_addresses=["alice@example.com"],
        )
        to, cc = _infer_recipients_all(msg, authenticated_email="me@example.com")
        assert to == "alice@example.com"
        assert cc is None  # Only alice + me, both excluded


# =============================================================================
# TOOL: do_reply_draft validation and wiring
# =============================================================================


class TestDoReplyDraftValidation:
    def test_missing_file_id(self) -> None:
        result = do_reply_draft(content="Reply body")
        assert result["error"] is True
        assert "file_id" in result["message"]

    def test_missing_content(self) -> None:
        result = do_reply_draft(file_id="thread_abc")
        assert result["error"] is True
        assert "content" in result["message"]


class TestDoReplyDraftSuccess:
    @patch("retry.time.sleep")
    @patch("tools.reply_draft.create_reply_draft")
    @patch("tools.reply_draft.fetch_thread")
    def test_returns_do_result(self, mock_fetch, mock_create, _sleep) -> None:
        mock_fetch.return_value = _make_thread()
        mock_create.return_value = ReplyDraftResult(
            draft_id="d1", message_id="m1", thread_id="thread_abc",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Re: Original Subject",
        )

        result = do_reply_draft(
            file_id="thread_abc",
            content="Thanks!",
        )

        assert isinstance(result, DoResult)
        assert result.operation == "reply_draft"
        assert result.file_id == "d1"
        assert "drafts" in result.web_link
        assert result.cues["thread_id"] == "thread_abc"
        assert result.cues["replying_to"] == "alice@example.com"

    @patch("retry.time.sleep")
    @patch("tools.reply_draft.create_reply_draft")
    @patch("tools.reply_draft.fetch_thread")
    def test_subject_gets_re_prefix(self, mock_fetch, mock_create, _sleep) -> None:
        mock_fetch.return_value = _make_thread(subject="Budget Review")
        mock_create.return_value = ReplyDraftResult(
            draft_id="d1", message_id="m1", thread_id="t1",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Re: Budget Review",
        )

        do_reply_draft(file_id="t1", content="Noted.")

        # Verify create_reply_draft was called with Re: prefix
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["subject"] == "Re: Budget Review"

    @patch("retry.time.sleep")
    @patch("tools.reply_draft.create_reply_draft")
    @patch("tools.reply_draft.fetch_thread")
    def test_threading_headers_passed(self, mock_fetch, mock_create, _sleep) -> None:
        msg = _make_message(
            message_id_header="<xyz@example.com>",
            references="<abc@example.com>",
        )
        mock_fetch.return_value = _make_thread(messages=[msg])
        mock_create.return_value = ReplyDraftResult(
            draft_id="d1", message_id="m1", thread_id="t1",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Re: Original Subject",
        )

        do_reply_draft(file_id="t1", content="Reply.")

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["in_reply_to"] == "<xyz@example.com>"
        assert call_kwargs["references"] == "<abc@example.com> <xyz@example.com>"

    @patch("retry.time.sleep")
    @patch("tools.reply_draft.create_reply_draft")
    @patch("tools.reply_draft.fetch_thread")
    def test_reply_all_infers_cc(self, mock_fetch, mock_create, _sleep) -> None:
        msg = _make_message(
            from_address="alice@example.com",
            to_addresses=["me@example.com", "bob@example.com"],
            cc_addresses=["carol@example.com"],
        )
        mock_fetch.return_value = _make_thread(messages=[msg])
        mock_create.return_value = ReplyDraftResult(
            draft_id="d1", message_id="m1", thread_id="t1",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Re: Original Subject",
            cc="bob@example.com, carol@example.com",
        )

        result = do_reply_draft(file_id="t1", content="Reply.", reply_all=True)

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["to"] == "alice@example.com"
        # Cc should include bob and carol but not alice (sender) or me (self)
        cc_value = call_kwargs["cc"]
        assert cc_value is not None
        assert "bob@example.com" in cc_value
        assert "carol@example.com" in cc_value

    @patch("retry.time.sleep")
    @patch("tools.reply_draft.create_reply_draft")
    @patch("tools.reply_draft.fetch_thread")
    def test_explicit_cc_overrides_inferred(self, mock_fetch, mock_create, _sleep) -> None:
        msg = _make_message(
            from_address="alice@example.com",
            to_addresses=["me@example.com", "bob@example.com"],
        )
        mock_fetch.return_value = _make_thread(messages=[msg])
        mock_create.return_value = ReplyDraftResult(
            draft_id="d1", message_id="m1", thread_id="t1",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Re: Original Subject",
            cc="explicit@example.com",
        )

        do_reply_draft(
            file_id="t1", content="Reply.",
            reply_all=True, cc="explicit@example.com",
        )

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["cc"] == "explicit@example.com"

    @patch("retry.time.sleep")
    @patch("tools.reply_draft.fetch_thread")
    def test_empty_thread_returns_error(self, mock_fetch, _sleep) -> None:
        mock_fetch.return_value = GmailThreadData(
            thread_id="t1", subject="Test", messages=[],
        )

        result = do_reply_draft(file_id="t1", content="Reply.")

        assert result["error"] is True
        assert "no messages" in result["message"]

    @patch("retry.time.sleep")
    @patch("tools.reply_draft.fetch_thread")
    def test_fetch_failure_returns_error(self, mock_fetch, _sleep) -> None:
        from models import MiseError, ErrorKind
        mock_fetch.side_effect = MiseError(ErrorKind.NOT_FOUND, "Thread not found")

        result = do_reply_draft(file_id="t1", content="Reply.")

        assert result["error"] is True
        assert result["kind"] == "not_found"

    @patch("retry.time.sleep")
    @patch("tools.reply_draft.create_reply_draft")
    @patch("tools.reply_draft.fetch_thread")
    def test_uses_last_message_for_threading(self, mock_fetch, mock_create, _sleep) -> None:
        """When thread has multiple messages, threading info comes from the last one."""
        first = _make_message(
            message_id="msg_1",
            from_address="alice@example.com",
            message_id_header="<first@example.com>",
        )
        second = _make_message(
            message_id="msg_2",
            from_address="bob@example.com",
            message_id_header="<second@example.com>",
            references="<first@example.com>",
        )
        mock_fetch.return_value = _make_thread(messages=[first, second])
        mock_create.return_value = ReplyDraftResult(
            draft_id="d1", message_id="m1", thread_id="t1",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="bob@example.com", subject="Re: Original Subject",
        )

        result = do_reply_draft(file_id="t1", content="Reply.")

        call_kwargs = mock_create.call_args[1]
        # Should reply to bob (last message), not alice (first)
        assert call_kwargs["to"] == "bob@example.com"
        assert call_kwargs["in_reply_to"] == "<second@example.com>"
        assert "<first@example.com> <second@example.com>" == call_kwargs["references"]


class TestDoReplyDraftWithInclude:
    @patch("retry.time.sleep")
    @patch("tools.reply_draft.create_reply_draft")
    @patch("tools.reply_draft.fetch_thread")
    @patch("tools.draft.get_file_metadata")
    def test_include_links_in_cues(self, mock_meta, mock_fetch, mock_create, _sleep) -> None:
        mock_meta.return_value = {
            "name": "Report",
            "mimeType": "application/vnd.google-apps.document",
            "webViewLink": "https://docs.google.com/document/d/xyz/edit",
        }
        mock_fetch.return_value = _make_thread()
        mock_create.return_value = ReplyDraftResult(
            draft_id="d1", message_id="m1", thread_id="t1",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Re: Original Subject",
            included_links=[IncludedLink(
                file_id="xyz", title="Report",
                mime_type="application/vnd.google-apps.document",
                web_link="https://docs.google.com/document/d/xyz/edit",
            )],
        )

        result = do_reply_draft(
            file_id="t1", content="See report.", include=["xyz"],
        )

        assert isinstance(result, DoResult)
        assert "included_links" in result.cues