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
from models import DoResult, EmailMessage, GmailThreadData, MiseError, ErrorKind
from tools.reply_draft import (
    _extract_email,
    _infer_recipients,
    _infer_recipients_all,
    do_reply_draft,
)

import pytest as _pytest


@_pytest.fixture(autouse=True)
def _stub_thread_draft_guard(monkeypatch):
    """Neutralise the superseded-draft guard (mise-sasivo) — it makes a live
    drafts.list call. Guard behaviour is asserted in TestSupersededDraftGuard,
    which re-patches explicitly; everywhere else the thread has no drafts."""
    monkeypatch.setattr("tools.reply_draft.list_thread_drafts", lambda thread_id: [])


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
    thread_id: str = "abc123def456abc1",
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
    @patch("adapters.gmail.get_sync_client")
    def test_passes_thread_id_to_api(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.post_json.return_value = {
            "id": "draft_reply",
            "message": {"id": "msg_reply"},
        }

        result = create_reply_draft(
            thread_id="abc123def456abc1",
            to="alice@example.com",
            subject="Re: Test",
            body_text="Reply",
            body_html="<p>Reply</p>",
            in_reply_to="<abc@example.com>",
            references="<abc@example.com>",
        )

        assert isinstance(result, ReplyDraftResult)
        assert result.draft_id == "draft_reply"
        assert result.thread_id == "abc123def456abc1"
        assert "draft_reply" in result.web_link

        # Verify threadId was in the API call body
        call_kwargs = mock_client.post_json.call_args[1]
        assert call_kwargs["json_body"]["message"]["threadId"] == "abc123def456abc1"

    @patch("retry.time.sleep")
    @patch("adapters.gmail.get_sync_client")
    def test_returns_correct_result_shape(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.post_json.return_value = {
            "id": "d1", "message": {"id": "m1"},
        }

        result = create_reply_draft(
            thread_id="a1b2c3d4e5f6a7b8",
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
        result = do_reply_draft(file_id="abc123def456abc1")
        assert result["error"] is True
        assert "content" in result["message"]


class TestDoReplyDraftSuccess:
    @patch("retry.time.sleep")
    @patch("tools.reply_draft.create_reply_draft")
    @patch("tools.reply_draft.fetch_thread")
    def test_returns_do_result(self, mock_fetch, mock_create, _sleep) -> None:
        mock_fetch.return_value = _make_thread()
        mock_create.return_value = ReplyDraftResult(
            draft_id="d1", message_id="m1", thread_id="abc123def456abc1",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Re: Original Subject",
        )

        result = do_reply_draft(
            file_id="abc123def456abc1",
            content="Thanks!",
        )

        assert isinstance(result, DoResult)
        assert result.operation == "reply_draft"
        assert result.file_id == "d1"
        assert "drafts" in result.web_link
        assert result.cues["thread_id"] == "abc123def456abc1"
        assert result.cues["replying_to"] == "alice@example.com"

    @patch("retry.time.sleep")
    @patch("tools.reply_draft.create_reply_draft")
    @patch("tools.reply_draft.fetch_thread")
    def test_subject_gets_re_prefix(self, mock_fetch, mock_create, _sleep) -> None:
        mock_fetch.return_value = _make_thread(subject="Budget Review")
        mock_create.return_value = ReplyDraftResult(
            draft_id="d1", message_id="m1", thread_id="a1b2c3d4e5f6a7b8",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Re: Budget Review",
        )

        do_reply_draft(file_id="a1b2c3d4e5f6a7b8", content="Noted.")

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
            draft_id="d1", message_id="m1", thread_id="a1b2c3d4e5f6a7b8",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Re: Original Subject",
        )

        do_reply_draft(file_id="a1b2c3d4e5f6a7b8", content="Reply.")

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["in_reply_to"] == "<xyz@example.com>"
        assert call_kwargs["references"] == "<abc@example.com> <xyz@example.com>"

    @patch("retry.time.sleep")
    @patch("tools.reply_draft.current_user_email", return_value="me@example.com")
    @patch("tools.reply_draft.create_reply_draft")
    @patch("tools.reply_draft.fetch_thread")
    def test_reply_all_infers_cc(self, mock_fetch, mock_create, _email, _sleep) -> None:
        msg = _make_message(
            from_address="alice@example.com",
            to_addresses=["me@example.com", "bob@example.com"],
            cc_addresses=["carol@example.com"],
        )
        mock_fetch.return_value = _make_thread(messages=[msg])
        mock_create.return_value = ReplyDraftResult(
            draft_id="d1", message_id="m1", thread_id="a1b2c3d4e5f6a7b8",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Re: Original Subject",
            cc="bob@example.com, carol@example.com",
        )

        result = do_reply_draft(file_id="a1b2c3d4e5f6a7b8", content="Reply.", reply_all=True)

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["to"] == "alice@example.com"
        # Cc should include bob and carol but not alice (sender) or me (self)
        cc_value = call_kwargs["cc"]
        assert cc_value is not None
        assert "bob@example.com" in cc_value
        assert "carol@example.com" in cc_value
        assert "alice@example.com" not in cc_value
        assert "me@example.com" not in cc_value

    @patch("retry.time.sleep")
    @patch("tools.reply_draft.current_user_email", return_value="me@example.com")
    @patch("tools.reply_draft.create_reply_draft")
    @patch("tools.reply_draft.fetch_thread")
    def test_reply_all_excludes_self_repro_shape(
        self, mock_fetch, mock_create, _email, _sleep
    ) -> None:
        # Field repro 2026-06-05 (thread 19e8e2fedd24cf5a): last message
        # From: external, To: me, Cc: second-external. reply_all must
        # produce To: sender, Cc: second-external only — not me.
        msg = _make_message(
            from_address="todd@external.example.com",
            to_addresses=["me@example.com"],
            cc_addresses=["robert@external.example.com"],
        )
        mock_fetch.return_value = _make_thread(messages=[msg])
        mock_create.return_value = ReplyDraftResult(
            draft_id="d1", message_id="m1", thread_id="a1b2c3d4e5f6a7b8",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="todd@external.example.com", subject="Re: Original Subject",
            cc="robert@external.example.com",
        )

        do_reply_draft(file_id="a1b2c3d4e5f6a7b8", content="Reply.", reply_all=True)

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["to"] == "todd@external.example.com"
        assert call_kwargs["cc"] == "robert@external.example.com"

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
            draft_id="d1", message_id="m1", thread_id="a1b2c3d4e5f6a7b8",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Re: Original Subject",
            cc="explicit@example.com",
        )

        do_reply_draft(
            file_id="a1b2c3d4e5f6a7b8", content="Reply.",
            reply_all=True, cc="explicit@example.com",
        )

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["cc"] == "explicit@example.com"

    @patch("retry.time.sleep")
    @patch("tools.reply_draft.fetch_thread")
    def test_empty_thread_returns_error(self, mock_fetch, _sleep) -> None:
        mock_fetch.return_value = GmailThreadData(
            thread_id="a1b2c3d4e5f6a7b8", subject="Test", messages=[],
        )

        result = do_reply_draft(file_id="a1b2c3d4e5f6a7b8", content="Reply.")

        assert result["error"] is True
        assert "no messages" in result["message"]

    @patch("retry.time.sleep")
    @patch("tools.reply_draft.fetch_thread")
    def test_fetch_failure_returns_error(self, mock_fetch, _sleep) -> None:
        from models import MiseError, ErrorKind
        mock_fetch.side_effect = MiseError(ErrorKind.NOT_FOUND, "Thread not found")

        result = do_reply_draft(file_id="a1b2c3d4e5f6a7b8", content="Reply.")

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
            draft_id="d1", message_id="m1", thread_id="a1b2c3d4e5f6a7b8",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="bob@example.com", subject="Re: Original Subject",
        )

        result = do_reply_draft(file_id="a1b2c3d4e5f6a7b8", content="Reply.")

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
            draft_id="d1", message_id="m1", thread_id="a1b2c3d4e5f6a7b8",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Re: Original Subject",
            included_links=[IncludedLink(
                file_id="xyz", title="Report",
                mime_type="application/vnd.google-apps.document",
                web_link="https://docs.google.com/document/d/xyz/edit",
            )],
        )

        result = do_reply_draft(
            file_id="a1b2c3d4e5f6a7b8", content="See report.", include=["xyz"],
        )

        assert isinstance(result, DoResult)
        assert "included_links" in result.cues

class TestReplyDraftSignature:
    """Reply drafts get the Gmail signature grace note too."""

    @patch("retry.time.sleep")
    @patch("tools.draft.get_primary_signature")
    @patch("tools.reply_draft.create_reply_draft")
    @patch("tools.reply_draft.fetch_thread")
    def test_signature_appended_to_reply(
        self, mock_fetch, mock_create, mock_sig, _sleep
    ) -> None:
        sig = '<div>Sam<br><a href="https://maps.example/q">Our office</a></div>'
        mock_sig.return_value = sig
        mock_fetch.return_value = _make_thread()
        mock_create.return_value = ReplyDraftResult(
            draft_id="d1", message_id="m1", thread_id="abc123def456abc1",
            web_link="https://mail.google.com/mail/#drafts/d1",
            to="alice@example.com", subject="Re: Original Subject",
        )

        result = do_reply_draft(file_id="abc123def456abc1", content="Thanks!")

        kwargs = mock_create.call_args[1]
        assert sig in kwargs["body_html"]
        assert "Our office (https://maps.example/q)" in kwargs["body_text"]
        assert isinstance(result, DoResult)
        assert result.cues["signature"] == "Gmail signature appended automatically"


class TestSupersededDraftGuard:
    """The superseded-draft guard (mise-sasivo)."""

    _EXISTING = [{"draft_id": "r111", "snippet": "Earlier staged reply", "internal_date": "1784752914000"}]

    @patch("tools.reply_draft.create_reply_draft")
    @patch("tools.reply_draft.list_thread_drafts")
    def test_existing_draft_refuses_with_teaching_error(self, mock_list, mock_create) -> None:
        mock_list.return_value = self._EXISTING
        result = do_reply_draft(file_id="19f8a9797b30561b", content="hi")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "r111" in result["message"]
        assert "supersede=True" in result["message"]
        assert "only ONE draft inline" in result["message"]
        mock_create.assert_not_called()

    @patch("retry.time.sleep")
    @patch("tools.reply_draft.create_reply_draft")
    @patch("tools.reply_draft.fetch_thread")
    @patch("tools.reply_draft._fetch_signature", return_value=("", "", []))
    @patch("tools.reply_draft.delete_draft")
    @patch("tools.reply_draft.list_thread_drafts")
    def test_supersede_discards_then_creates(
        self, mock_list, mock_delete, _sig, mock_fetch, mock_create, _sleep
    ) -> None:
        mock_list.return_value = self._EXISTING
        mock_fetch.return_value = MagicMock(
            subject="Re: test",
            messages=[MagicMock(from_address="a@b.com", to_addresses=[], cc_addresses=[])],
        )
        mock_create.return_value = MagicMock(draft_id="r222", web_link="link")
        result = do_reply_draft(file_id="19f8a9797b30561b", content="hi", supersede=True)
        mock_delete.assert_called_once_with("r111")
        assert result.cues["superseded_drafts"] == ["r111"]
        mock_create.assert_called_once()

    @patch("retry.time.sleep")
    @patch("tools.reply_draft.create_reply_draft")
    @patch("tools.reply_draft.fetch_thread")
    @patch("tools.reply_draft._fetch_signature", return_value=("", "", []))
    @patch("tools.reply_draft.list_thread_drafts", side_effect=RuntimeError("api down"))
    def test_check_failure_fails_open_with_warning(
        self, mock_list, _sig, mock_fetch, mock_create, _sleep
    ) -> None:
        mock_fetch.return_value = MagicMock(
            subject="Re: test",
            messages=[MagicMock(from_address="a@b.com", to_addresses=[], cc_addresses=[])],
        )
        mock_create.return_value = MagicMock(draft_id="r333", web_link="link")
        result = do_reply_draft(file_id="19f8a9797b30561b", content="hi")
        assert any("Could not check for existing drafts" in w for w in result.cues["warnings"])
        mock_create.assert_called_once()

    @patch("tools.reply_draft.delete_draft", side_effect=MiseError(ErrorKind.PERMISSION_DENIED, "nope"))
    @patch("tools.reply_draft.list_thread_drafts")
    def test_supersede_delete_failure_is_an_error(self, mock_list, mock_delete) -> None:
        mock_list.return_value = self._EXISTING
        result = do_reply_draft(file_id="19f8a9797b30561b", content="hi", supersede=True)
        assert result["error"] is True
        assert "supersede failed" in result["message"]


class TestListThreadDrafts:
    """Adapter: list_thread_drafts (the guard's eyes)."""

    @patch("adapters.gmail.get_sync_client")
    def test_filters_by_thread_and_fetches_snippets(self, mock_get_client) -> None:
        from adapters.gmail import list_thread_drafts
        import orjson
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_bytes.return_value = orjson.dumps({
            "drafts": [
                {"id": "r1", "message": {"threadId": "thread_A"}},
                {"id": "r2", "message": {"threadId": "thread_B"}},
                {"id": "r3", "message": {"threadId": "thread_A"}},
            ]
        })
        mock_client.get_json.return_value = {"id": "x", "message": {"snippet": "hello", "internalDate": "123"}}
        with patch("retry.time.sleep"):
            result = list_thread_drafts("thread_A")
        assert [d["draft_id"] for d in result] == ["r1", "r3"]
        assert result[0]["snippet"] == "hello"
        assert mock_client.get_json.call_count == 2  # snippet fetch per match

    @patch("adapters.gmail.get_sync_client")
    def test_zero_length_body_means_no_drafts(self, mock_get_client) -> None:
        """Gmail returns an EMPTY body (not {}) for zero drafts under a fields mask."""
        from adapters.gmail import list_thread_drafts
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_bytes.return_value = b""
        with patch("retry.time.sleep"):
            assert list_thread_drafts("thread_A") == []
        mock_client.get_json.assert_not_called()

    @patch("adapters.gmail.get_sync_client")
    def test_follows_pagination(self, mock_get_client) -> None:
        from adapters.gmail import list_thread_drafts
        import orjson
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_bytes.side_effect = [
            orjson.dumps({"drafts": [{"id": "r1", "message": {"threadId": "T"}}], "nextPageToken": "tok"}),
            orjson.dumps({"drafts": [{"id": "r2", "message": {"threadId": "T"}}]}),
        ]
        mock_client.get_json.return_value = {"message": {"snippet": "", "internalDate": ""}}
        with patch("retry.time.sleep"):
            result = list_thread_drafts("T")
        assert [d["draft_id"] for d in result] == ["r1", "r2"]
        assert mock_client.get_bytes.call_args_list[1].kwargs["params"]["pageToken"] == "tok"
