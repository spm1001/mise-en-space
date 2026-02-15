"""Tests for move operation."""

from unittest.mock import patch, MagicMock

from server import do
from tools.move import do_move


class TestDoMoveValidation:
    """Input validation via do() wrapper."""

    def test_move_without_file_id_returns_error(self) -> None:
        result = do(operation="move", destination_folder_id="folder1")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "file_id" in result["message"]

    def test_move_without_destination_returns_error(self) -> None:
        result = do(operation="move", file_id="file1")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "destination_folder_id" in result["message"]


class TestDoMove:
    """Move logic with mocked Drive API."""

    @patch("retry.time.sleep")
    @patch("tools.move.get_drive_service")
    def test_moves_file(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        # Current file has one parent
        mock_service.files().get().execute.side_effect = [
            {
                "id": "file1",
                "name": "Report.pdf",
                "parents": ["old_folder"],
                "webViewLink": "https://drive.google.com/file/d/file1/view",
            },
            # Destination folder name lookup
            {"name": "Archive"},
        ]
        mock_service.files().update().execute.return_value = {
            "id": "file1",
            "name": "Report.pdf",
            "parents": ["new_folder"],
            "webViewLink": "https://drive.google.com/file/d/file1/view",
        }

        result = do_move("file1", "new_folder")

        assert result["file_id"] == "file1"
        assert result["title"] == "Report.pdf"
        assert result["operation"] == "move"
        assert result["cues"]["destination_folder"] == "Archive"
        assert result["cues"]["previous_parents"] == ["old_folder"]

    @patch("retry.time.sleep")
    @patch("tools.move.get_drive_service")
    def test_moves_file_with_multiple_parents(self, mock_svc, _sleep) -> None:
        """Removes all existing parents (single-parent enforcement)."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_service.files().get().execute.side_effect = [
            {
                "id": "file1",
                "name": "Report.pdf",
                "parents": ["folder_a", "folder_b"],
                "webViewLink": "https://drive.google.com/file/d/file1/view",
            },
            {"name": "Target"},
        ]
        mock_service.files().update().execute.return_value = {
            "id": "file1",
            "name": "Report.pdf",
            "parents": ["target_folder"],
            "webViewLink": "https://drive.google.com/file/d/file1/view",
        }

        result = do_move("file1", "target_folder")

        assert result["cues"]["previous_parents"] == ["folder_a", "folder_b"]
        # Verify removeParents included both old parents
        update_call = mock_service.files().update.call_args
        assert "folder_a,folder_b" == update_call.kwargs.get("removeParents", "")

    @patch("retry.time.sleep")
    @patch("tools.move.get_drive_service")
    def test_move_file_not_found(self, mock_svc, _sleep) -> None:
        """Drive API 404 becomes a clean error."""
        from googleapiclient.errors import HttpError
        import httplib2

        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        resp = httplib2.Response({"status": "404"})
        mock_service.files().get().execute.side_effect = HttpError(
            resp, b"File not found"
        )

        result = do_move("nonexistent", "folder1")

        assert result["error"] is True

    @patch("retry.time.sleep")
    @patch("tools.move.get_drive_service")
    def test_move_routes_through_do(self, mock_svc, _sleep) -> None:
        """do(operation='move') routes to do_move."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_service.files().get().execute.side_effect = [
            {"id": "f1", "name": "Test", "parents": ["old"], "webViewLink": ""},
            {"name": "Dest"},
        ]
        mock_service.files().update().execute.return_value = {
            "id": "f1", "name": "Test", "parents": ["new"], "webViewLink": "",
        }

        result = do(operation="move", file_id="f1", destination_folder_id="new")

        assert result["file_id"] == "f1"
        assert result["operation"] == "move"
