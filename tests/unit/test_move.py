"""Tests for move operation."""

from unittest.mock import patch, MagicMock, Mock

import httpx

from models import DoResult
from server import do
from tools.move import do_move


class TestDoMoveValidation:
    """Input validation via do_move() and do() wrapper."""

    def test_move_without_file_id_returns_error(self) -> None:
        result = do_move(destination_folder_id="folder1")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "file_id" in result["message"]

    def test_move_without_destination_returns_error(self) -> None:
        result = do_move(file_id="file1")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "destination_folder_id" in result["message"]

    def test_move_without_both_returns_error(self) -> None:
        result = do_move()
        assert result["error"] is True
        assert "file_id" in result["message"]

    def test_move_validation_through_do(self) -> None:
        result = do(operation="move", file_id="file1")
        assert result["error"] is True
        assert "destination_folder_id" in result["message"]

    def test_rejects_bad_file_id(self) -> None:
        result = do_move(file_id="bad id!", destination_folder_id="folder1")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"

    def test_rejects_bad_destination_id(self) -> None:
        result = do_move(file_id="abc123", destination_folder_id="has spaces")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"


class TestDoMove:
    """Move logic with mocked Drive API."""

    @patch("retry.time.sleep")
    @patch("tools.move.get_sync_client")
    def test_moves_file(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_json.side_effect = [
            # Destination folder validation
            {"mimeType": "application/vnd.google-apps.folder", "name": "Archive"},
            # Current file parents
            {
                "id": "file1",
                "name": "Report.pdf",
                "parents": ["old_folder"],
                "webViewLink": "https://drive.google.com/file/d/file1/view",
            },
        ]
        mock_client.patch_json.return_value = {
            "id": "file1",
            "name": "Report.pdf",
            "parents": ["new_folder"],
            "webViewLink": "https://drive.google.com/file/d/file1/view",
        }

        result = do_move("file1", "new_folder")

        assert isinstance(result, DoResult)
        assert result.file_id == "file1"
        assert result.title == "Report.pdf"
        assert result.operation == "move"
        assert result.cues["destination_folder"] == "Archive"
        assert result.cues["previous_parents"] == ["old_folder"]

    @patch("retry.time.sleep")
    @patch("tools.move.get_sync_client")
    def test_moves_file_with_multiple_parents(self, mock_get_client, _sleep) -> None:
        """Removes all existing parents (single-parent enforcement)."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_json.side_effect = [
            {"mimeType": "application/vnd.google-apps.folder", "name": "Target"},
            {
                "id": "file1",
                "name": "Report.pdf",
                "parents": ["folder_a", "folder_b"],
                "webViewLink": "https://drive.google.com/file/d/file1/view",
            },
        ]
        mock_client.patch_json.return_value = {
            "id": "file1",
            "name": "Report.pdf",
            "parents": ["target_folder"],
            "webViewLink": "https://drive.google.com/file/d/file1/view",
        }

        result = do_move("file1", "target_folder")

        assert isinstance(result, DoResult)
        assert result.cues["previous_parents"] == ["folder_a", "folder_b"]
        # Verify removeParents included both old parents
        patch_call = mock_client.patch_json.call_args
        params = patch_call[1].get("params", patch_call[0][1] if len(patch_call[0]) > 1 else {})
        assert params["removeParents"] == "folder_a,folder_b"

    @patch("retry.time.sleep")
    @patch("tools.move.get_sync_client")
    def test_move_file_not_found(self, mock_get_client, _sleep) -> None:
        """Drive API 404 becomes a clean error."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_response = Mock()
        mock_response.status_code = 404
        mock_client.get_json.side_effect = httpx.HTTPStatusError(
            "File not found", request=Mock(), response=mock_response
        )

        result = do_move("nonexistent", "folder1")

        assert result["error"] is True

    @patch("retry.time.sleep")
    @patch("tools.move.get_sync_client")
    def test_move_routes_through_do(self, mock_get_client, _sleep) -> None:
        """do(operation='move') routes to do_move and returns dict."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_json.side_effect = [
            {"mimeType": "application/vnd.google-apps.folder", "name": "Dest"},
            {"id": "f1", "name": "Test", "parents": ["old"], "webViewLink": ""},
        ]
        mock_client.patch_json.return_value = {
            "id": "f1", "name": "Test", "parents": ["new"], "webViewLink": "",
        }

        result = do(operation="move", file_id="f1", destination_folder_id="new")

        assert result["file_id"] == "f1"
        assert result["operation"] == "move"

    @patch("retry.time.sleep")
    @patch("tools.move.get_sync_client")
    def test_move_rejects_non_folder_destination(self, mock_get_client, _sleep) -> None:
        """Passing a file ID as destination returns a clear error."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_json.return_value = {
            "mimeType": "application/vnd.google-apps.document",
            "name": "Some Doc",
        }

        result = do_move("file1", "not_a_folder")

        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "not a folder" in result["message"]
        assert "Some Doc" in result["message"]

    @patch("retry.time.sleep")
    @patch("tools.move.get_sync_client")
    def test_move_rejects_spreadsheet_destination(self, mock_get_client, _sleep) -> None:
        """Any non-folder MIME type is rejected."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_json.return_value = {
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "name": "Budget",
        }

        result = do_move("file1", "sheet_id")

        assert result["error"] is True
        assert result["kind"] == "invalid_input"
