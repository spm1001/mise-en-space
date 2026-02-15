"""Tests for do tool (create and future operations)."""

from unittest.mock import patch, MagicMock

import pytest

from models import CreateResult, CreateError
from server import do
from tools.create import do_create, DOC_TYPE_TO_MIME


class TestDoToolRouting:
    """MCP do() wrapper routes operations correctly."""

    def test_unknown_operation_returns_error(self) -> None:
        result = do(operation="explode")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "explode" in result["message"]

    def test_create_without_content_returns_error(self) -> None:
        result = do(operation="create", title="Title")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "content" in result["message"]

    def test_create_without_title_returns_error(self) -> None:
        result = do(operation="create", content="# Hello")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "title" in result["message"]

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_create_routes_to_do_create(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "doc1",
            "webViewLink": "https://docs.google.com/document/d/doc1/edit",
            "name": "Test",
        }

        result = do(operation="create", content="# Test", title="Test")

        assert result["file_id"] == "doc1"
        assert result["type"] == "doc"

    def test_default_operation_is_create(self) -> None:
        """Calling do() without operation should default to create."""
        result = do(content=None, title=None)
        # Should hit the create validation (missing content/title), not unknown operation
        assert result["kind"] == "invalid_input"
        assert "content" in result["message"]


class TestDoCreateValidation:
    """Input validation before API calls."""

    def test_invalid_doc_type_returns_error(self) -> None:
        result = do_create("content", "Title", doc_type="invalid")
        assert isinstance(result, CreateError)
        assert result.kind == "invalid_input"

    def test_sheet_not_implemented(self) -> None:
        result = do_create("content", "Title", doc_type="sheet")
        assert isinstance(result, CreateError)
        assert result.kind == "not_implemented"

    def test_slides_not_implemented(self) -> None:
        result = do_create("content", "Title", doc_type="slides")
        assert isinstance(result, CreateError)
        assert result.kind == "not_implemented"


class TestDoCreateDoc:
    """Test doc creation with mocked Drive API."""

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_creates_doc(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "new_doc_id",
            "webViewLink": "https://docs.google.com/document/d/new_doc_id/edit",
            "name": "My Document",
        }

        result = do_create("# Hello", "My Document")

        assert isinstance(result, CreateResult)
        assert result.file_id == "new_doc_id"
        assert result.web_link == "https://docs.google.com/document/d/new_doc_id/edit"
        assert result.title == "My Document"
        assert result.doc_type == "doc"

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_creates_doc_with_folder(self, mock_svc, _sleep) -> None:
        """folder_id passed as parent."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "doc1",
            "webViewLink": "https://docs.google.com/document/d/doc1/edit",
            "name": "In Folder",
        }

        result = do_create("content", "In Folder", folder_id="folder123")

        assert isinstance(result, CreateResult)

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_missing_name_uses_title(self, mock_svc, _sleep) -> None:
        """If API doesn't return name, falls back to provided title."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "doc1",
            "webViewLink": "https://docs.google.com/document/d/doc1/edit",
        }

        result = do_create("content", "Fallback Title")

        assert isinstance(result, CreateResult)
        assert result.title == "Fallback Title"


class TestDocTypeMapping:
    """Verify doc type constants."""

    def test_all_types_mapped(self) -> None:
        assert "doc" in DOC_TYPE_TO_MIME
        assert "sheet" in DOC_TYPE_TO_MIME
        assert "slides" in DOC_TYPE_TO_MIME
