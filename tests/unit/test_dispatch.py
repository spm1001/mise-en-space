"""Tests for do() dispatch infrastructure."""

from unittest.mock import patch, MagicMock

from models import DoResult
from server import _DISPATCH, do
from tools import OPERATIONS


class TestDispatchConstant:
    """OPERATIONS constant and _DISPATCH dict stay in sync."""

    def test_operations_matches_dispatch_keys(self) -> None:
        """Every operation in OPERATIONS has a dispatch handler, and vice versa."""
        assert set(OPERATIONS) == set(_DISPATCH.keys())

    def test_operations_is_frozenset(self) -> None:
        """OPERATIONS is immutable."""
        assert isinstance(OPERATIONS, frozenset)

    def test_unknown_operation_returns_error(self) -> None:
        result = do(operation="explode")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "explode" in result["message"]
        # Error message lists supported operations
        for op in OPERATIONS:
            assert op in result["message"]


class TestAllOperationsReturnDoResult:
    """Every operation returns DoResult on success (not raw dict)."""

    @patch("retry.time.sleep")
    @patch("tools.move.get_drive_service")
    def test_move_returns_do_result(self, mock_svc, _sleep) -> None:
        from tools.move import do_move

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().get().execute.side_effect = [
            {"mimeType": "application/vnd.google-apps.folder", "name": "Dest"},
            {"id": "f1", "name": "Test", "parents": ["old"], "webViewLink": ""},
        ]
        mock_service.files().update().execute.return_value = {
            "id": "f1", "name": "Test", "parents": ["new"], "webViewLink": "",
        }

        result = do_move("f1", "new")
        assert isinstance(result, DoResult)
        assert result.operation == "move"

    @patch("retry.time.sleep")
    @patch("tools.edit.get_docs_service")
    def test_prepend_returns_do_result(self, mock_svc, _sleep) -> None:
        from tools.edit import do_prepend

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.documents().get().execute.return_value = {
            "title": "Doc", "body": {"content": [{"endIndex": 50}]},
        }

        result = do_prepend("doc1", "hello")
        assert isinstance(result, DoResult)
        assert result.operation == "prepend"

    @patch("retry.time.sleep")
    @patch("tools.edit.get_docs_service")
    def test_append_returns_do_result(self, mock_svc, _sleep) -> None:
        from tools.edit import do_append

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.documents().get().execute.return_value = {
            "title": "Doc", "body": {"content": [{"endIndex": 50}]},
        }

        result = do_append("doc1", "hello")
        assert isinstance(result, DoResult)
        assert result.operation == "append"

    @patch("retry.time.sleep")
    @patch("tools.edit.get_docs_service")
    def test_replace_text_returns_do_result(self, mock_svc, _sleep) -> None:
        from tools.edit import do_replace_text

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.documents().get().execute.return_value = {
            "title": "Doc", "body": {"content": [{"endIndex": 50}]},
        }
        mock_service.documents().batchUpdate().execute.return_value = {
            "replies": [{"replaceAllText": {"occurrencesChanged": 1}}],
        }

        result = do_replace_text("doc1", "old", "new")
        assert isinstance(result, DoResult)
        assert result.operation == "replace_text"

    @patch("retry.time.sleep")
    @patch("tools.overwrite.get_docs_service")
    def test_overwrite_returns_do_result(self, mock_svc, _sleep) -> None:
        from tools.overwrite import do_overwrite

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.documents().get().execute.return_value = {
            "title": "Doc", "body": {"content": [{"endIndex": 1}]},
        }

        result = do_overwrite(file_id="doc1", content="hello")
        assert isinstance(result, DoResult)
        assert result.operation == "overwrite"

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_create_returns_do_result(self, mock_svc, _sleep) -> None:
        from tools.create import do_create

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "doc1",
            "webViewLink": "https://docs.google.com/document/d/doc1/edit",
            "name": "Test",
        }

        result = do_create("# Test", "Test")
        assert isinstance(result, DoResult)
        assert result.operation == "create"


class TestDoResultToDictRoundTrip:
    """DoResult.to_dict() produces the expected MCP response shape."""

    def test_basic_to_dict(self) -> None:
        result = DoResult(
            file_id="f1", title="Test", web_link="https://example.com",
            operation="move", cues={"key": "val"},
        )
        d = result.to_dict()
        assert d == {
            "file_id": "f1", "title": "Test", "web_link": "https://example.com",
            "operation": "move", "cues": {"key": "val"},
        }

    def test_extras_merged_into_dict(self) -> None:
        result = DoResult(
            file_id="f1", title="Test", web_link="https://example.com",
            operation="create", cues={}, extras={"type": "doc"},
        )
        d = result.to_dict()
        assert d["type"] == "doc"
        assert d["operation"] == "create"
