"""Tests for do_comment_reply operation."""

from unittest.mock import patch

from models import CommentReply, DoResult, MiseError, ErrorKind
from tools.comment_reply import do_comment_reply


def _fake_reply(reply_id: str = "r1", content: str = "[agent] ok") -> CommentReply:
    return CommentReply(id=reply_id, content=content, author_name="Claude")


class TestValidation:
    def test_missing_file_id(self) -> None:
        result = do_comment_reply(comment_id="c1", content="hi")
        assert result["error"] is True
        assert "file_id" in result["message"]

    def test_missing_comment_id(self) -> None:
        result = do_comment_reply(file_id="f1", content="hi")
        assert result["error"] is True
        assert "comment_id" in result["message"]

    def test_neither_content_nor_action(self) -> None:
        result = do_comment_reply(file_id="f1", comment_id="c1")
        assert result["error"] is True
        assert "content" in result["message"]

    def test_whitespace_only_content_treated_as_absent(self) -> None:
        result = do_comment_reply(file_id="f1", comment_id="c1", content="   ")
        assert result["error"] is True
        assert "content" in result["message"]

    def test_invalid_action(self) -> None:
        result = do_comment_reply(file_id="f1", comment_id="c1", action="delete")
        assert result["error"] is True
        assert "action" in result["message"]

    def test_invalid_file_id(self) -> None:
        result = do_comment_reply(file_id="bad id", comment_id="c1", content="hi")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"


class TestAgentPrefix:
    @patch("tools.comment_reply.reply_to_comment")
    def test_prefixes_content(self, mock_reply) -> None:
        mock_reply.return_value = _fake_reply()
        do_comment_reply(file_id="f1", comment_id="c1", content="Looks good")
        # adapter receives the prefixed content
        assert mock_reply.call_args.kwargs["content"] == "[agent] Looks good"

    @patch("tools.comment_reply.reply_to_comment")
    def test_does_not_double_prefix(self, mock_reply) -> None:
        mock_reply.return_value = _fake_reply()
        do_comment_reply(file_id="f1", comment_id="c1", content="[agent] already")
        assert mock_reply.call_args.kwargs["content"] == "[agent] already"

    @patch("tools.comment_reply.reply_to_comment")
    def test_resolve_only_passes_no_content(self, mock_reply) -> None:
        mock_reply.return_value = _fake_reply(content="")
        do_comment_reply(file_id="f1", comment_id="c1", action="resolve")
        assert mock_reply.call_args.kwargs["content"] is None
        assert mock_reply.call_args.kwargs["action"] == "resolve"


class TestSuccess:
    @patch("tools.comment_reply.reply_to_comment")
    def test_returns_do_result(self, mock_reply) -> None:
        mock_reply.return_value = _fake_reply(reply_id="reply9")
        result = do_comment_reply(file_id="f1", comment_id="c1", content="hi")
        assert isinstance(result, DoResult)
        assert result.operation == "comment_reply"
        assert result.cues["reply_id"] == "reply9"
        assert result.cues["comment_id"] == "c1"

    @patch("tools.comment_reply.reply_to_comment")
    def test_resolve_cue_describes_resolution(self, mock_reply) -> None:
        mock_reply.return_value = _fake_reply(content="")
        result = do_comment_reply(file_id="f1", comment_id="c1", action="resolve")
        assert "resolved" in result.cues["action"].lower()

    @patch("tools.comment_reply.reply_to_comment")
    def test_adapter_error_becomes_error_dict(self, mock_reply) -> None:
        mock_reply.side_effect = MiseError(ErrorKind.NOT_FOUND, "Comment c1 not found")
        result = do_comment_reply(file_id="f1", comment_id="c1", content="hi")
        assert result["error"] is True
        assert result["kind"] == "not_found"
