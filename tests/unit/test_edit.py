"""Tests for surgical edit operations (prepend, append, replace_text)."""

from unittest.mock import patch, MagicMock

from server import do
from tools.edit import do_prepend, do_append, do_replace_text


def _mock_docs_service(end_index: int = 50, title: str = "Test Doc"):
    """Create a mock Docs service with standard document response."""
    mock_service = MagicMock()
    mock_service.documents().get().execute.return_value = {
        "title": title,
        "body": {"content": [{"endIndex": 1}, {"endIndex": end_index}]},
    }
    return mock_service


class TestDoPrependValidation:
    def test_prepend_without_file_id_returns_error(self) -> None:
        result = do(operation="prepend", content="hello")
        assert result["error"] is True
        assert "file_id" in result["message"]

    def test_prepend_empty_content_returns_error(self) -> None:
        result = do_prepend("doc123", "")
        assert result["error"] is True
        assert "content" in result["message"]


class TestDoPrepend:
    @patch("retry.time.sleep")
    @patch("tools.edit.get_docs_service")
    def test_prepends_text(self, mock_svc, _sleep) -> None:
        mock_service = _mock_docs_service()
        mock_svc.return_value = mock_service

        result = do_prepend("doc123", "Executive Summary\n\n")

        assert result["file_id"] == "doc123"
        assert result["operation"] == "prepend"
        assert result["cues"]["inserted_chars"] == 19

        # Verify insertText at index 1
        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]
        insert_req = requests[0]["insertText"]
        assert insert_req["location"]["index"] == 1
        assert insert_req["text"] == "Executive Summary\n\n"

    @patch("retry.time.sleep")
    @patch("tools.edit.get_docs_service")
    def test_prepend_routes_through_do(self, mock_svc, _sleep) -> None:
        mock_svc.return_value = _mock_docs_service()

        result = do(operation="prepend", file_id="doc1", content="Hello\n")

        assert result["operation"] == "prepend"
        assert result["file_id"] == "doc1"


class TestDoAppendValidation:
    def test_append_without_file_id_returns_error(self) -> None:
        result = do(operation="append", content="hello")
        assert result["error"] is True
        assert "file_id" in result["message"]

    def test_append_empty_content_returns_error(self) -> None:
        result = do_append("doc123", "")
        assert result["error"] is True
        assert "content" in result["message"]


class TestDoAppend:
    @patch("retry.time.sleep")
    @patch("tools.edit.get_docs_service")
    def test_appends_text(self, mock_svc, _sleep) -> None:
        mock_service = _mock_docs_service(end_index=100)
        mock_svc.return_value = mock_service

        result = do_append("doc123", "\n\n--- Notes ---")

        assert result["operation"] == "append"
        assert result["cues"]["inserted_chars"] == 15

        # Verify insertText at endIndex - 1
        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]
        insert_req = requests[0]["insertText"]
        assert insert_req["location"]["index"] == 99  # endIndex - 1

    @patch("retry.time.sleep")
    @patch("tools.edit.get_docs_service")
    def test_append_to_empty_doc(self, mock_svc, _sleep) -> None:
        mock_service = _mock_docs_service(end_index=1)
        mock_svc.return_value = mock_service

        result = do_append("doc123", "First content")

        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]
        # Empty doc: max(1-1, 1) = 1
        assert requests[0]["insertText"]["location"]["index"] == 1

    @patch("retry.time.sleep")
    @patch("tools.edit.get_docs_service")
    def test_append_routes_through_do(self, mock_svc, _sleep) -> None:
        mock_svc.return_value = _mock_docs_service()

        result = do(operation="append", file_id="doc1", content="Tail\n")

        assert result["operation"] == "append"


class TestDoReplaceTextValidation:
    def test_replace_without_file_id_returns_error(self) -> None:
        result = do(operation="replace_text", find="old", content="new")
        assert result["error"] is True
        assert "file_id" in result["message"]

    def test_replace_without_find_returns_error(self) -> None:
        result = do(operation="replace_text", file_id="doc1", content="new")
        assert result["error"] is True
        assert "find" in result["message"]

    def test_replace_without_content_returns_error(self) -> None:
        result = do_replace_text("doc123", "old", None)
        assert result["error"] is True
        assert "content" in result["message"]


class TestDoReplaceText:
    @patch("retry.time.sleep")
    @patch("tools.edit.get_docs_service")
    def test_replaces_text(self, mock_svc, _sleep) -> None:
        mock_service = _mock_docs_service()
        mock_svc.return_value = mock_service

        mock_service.documents().batchUpdate().execute.return_value = {
            "replies": [{"replaceAllText": {"occurrencesChanged": 3}}],
        }

        result = do_replace_text("doc123", "DRAFT", "FINAL")

        assert result["operation"] == "replace_text"
        assert result["cues"]["find"] == "DRAFT"
        assert result["cues"]["replace"] == "FINAL"
        assert result["cues"]["occurrences_changed"] == 3

    @patch("retry.time.sleep")
    @patch("tools.edit.get_docs_service")
    def test_replace_with_empty_string_deletes(self, mock_svc, _sleep) -> None:
        """Empty replace string effectively deletes all matches."""
        mock_service = _mock_docs_service()
        mock_svc.return_value = mock_service

        mock_service.documents().batchUpdate().execute.return_value = {
            "replies": [{"replaceAllText": {"occurrencesChanged": 2}}],
        }

        result = do_replace_text("doc123", "remove me", "")

        assert result["cues"]["replace"] == ""
        assert result["cues"]["occurrences_changed"] == 2

    @patch("retry.time.sleep")
    @patch("tools.edit.get_docs_service")
    def test_replace_routes_through_do(self, mock_svc, _sleep) -> None:
        mock_service = _mock_docs_service()
        mock_svc.return_value = mock_service
        mock_service.documents().batchUpdate().execute.return_value = {
            "replies": [{"replaceAllText": {"occurrencesChanged": 1}}],
        }

        result = do(operation="replace_text", file_id="doc1", find="old", content="new")

        assert result["operation"] == "replace_text"

    @patch("retry.time.sleep")
    @patch("tools.edit.get_docs_service")
    def test_replace_zero_occurrences(self, mock_svc, _sleep) -> None:
        mock_service = _mock_docs_service()
        mock_svc.return_value = mock_service
        mock_service.documents().batchUpdate().execute.return_value = {
            "replies": [{"replaceAllText": {"occurrencesChanged": 0}}],
        }

        result = do_replace_text("doc123", "nonexistent", "replacement")

        assert result["cues"]["occurrences_changed"] == 0


class TestEditErrorPaths:
    """API errors surface cleanly for all edit operations."""

    @patch("retry.time.sleep")
    @patch("tools.edit.get_docs_service")
    def test_prepend_doc_not_found(self, mock_svc, _sleep) -> None:
        from googleapiclient.errors import HttpError
        import httplib2

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        resp = httplib2.Response({"status": "404"})
        mock_service.documents().get().execute.side_effect = HttpError(resp, b"Not found")

        result = do_prepend("nonexistent", "hello")

        assert result["error"] is True

    @patch("retry.time.sleep")
    @patch("tools.edit.get_docs_service")
    def test_append_permission_denied(self, mock_svc, _sleep) -> None:
        from googleapiclient.errors import HttpError
        import httplib2

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        resp = httplib2.Response({"status": "403"})
        mock_service.documents().get().execute.side_effect = HttpError(resp, b"Forbidden")

        result = do_append("readonly", "hello")

        assert result["error"] is True

    @patch("retry.time.sleep")
    @patch("tools.edit.get_docs_service")
    def test_replace_text_doc_not_found(self, mock_svc, _sleep) -> None:
        from googleapiclient.errors import HttpError
        import httplib2

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        resp = httplib2.Response({"status": "404"})
        mock_service.documents().get().execute.side_effect = HttpError(resp, b"Not found")

        result = do_replace_text("nonexistent", "find", "replace")

        assert result["error"] is True
