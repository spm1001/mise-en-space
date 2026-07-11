"""Tests for do_comment operation (open a new, unanchored comment thread)."""

from unittest.mock import patch

from models import CommentData, DoResult, MiseError, ErrorKind
from tools.comment import do_comment


def _fake_comment(comment_id: str = "c9", content: str = "[agent] hi") -> CommentData:
    return CommentData(id=comment_id, content=content, author_name="Claude")


class TestValidation:
    def test_missing_file_id(self) -> None:
        result = do_comment(content="hi")
        assert result["error"] is True
        assert "file_id" in result["message"]

    def test_missing_content(self) -> None:
        result = do_comment(file_id="f1")
        assert result["error"] is True
        assert "content" in result["message"]

    def test_whitespace_only_content_treated_as_absent(self) -> None:
        result = do_comment(file_id="f1", content="   ")
        assert result["error"] is True
        assert "content" in result["message"]

    def test_invalid_file_id(self) -> None:
        result = do_comment(file_id="bad id", content="hi")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"


class TestAgentPrefix:
    @patch("tools.comment.create_comment")
    def test_prefixes_content(self, mock_create) -> None:
        mock_create.return_value = _fake_comment()
        do_comment(file_id="f1", content="Looks stale")
        # adapter receives the prefixed content (positional: file_id, content)
        assert mock_create.call_args.args[1] == "[agent] Looks stale"

    @patch("tools.comment.create_comment")
    def test_does_not_double_prefix(self, mock_create) -> None:
        mock_create.return_value = _fake_comment()
        do_comment(file_id="f1", content="[agent] already")
        assert mock_create.call_args.args[1] == "[agent] already"


class TestSuccess:
    @patch("tools.comment.create_comment")
    def test_returns_do_result(self, mock_create) -> None:
        mock_create.return_value = _fake_comment(comment_id="cmt42")
        result = do_comment(file_id="f1", content="hi")
        assert isinstance(result, DoResult)
        assert result.operation == "comment"
        assert result.cues["comment_id"] == "cmt42"

    @patch("tools.comment.create_comment")
    def test_adapter_error_becomes_error_dict(self, mock_create) -> None:
        mock_create.side_effect = MiseError(ErrorKind.PERMISSION_DENIED, "No permission")
        result = do_comment(file_id="f1", content="hi")
        assert result["error"] is True
        assert result["kind"] == "permission_denied"
