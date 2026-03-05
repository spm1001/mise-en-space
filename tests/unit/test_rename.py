"""Tests for do_rename operation."""

from unittest.mock import patch, MagicMock

from models import DoResult, MiseError, ErrorKind
from tools.rename import do_rename


class TestDoRenameValidation:
    """Parameter validation for rename."""

    def test_missing_file_id(self) -> None:
        result = do_rename(title="New Name")
        assert result["error"] is True
        assert "file_id" in result["message"]

    def test_missing_title(self) -> None:
        result = do_rename(file_id="f1")
        assert result["error"] is True
        assert "title" in result["message"]

    def test_missing_both(self) -> None:
        result = do_rename()
        assert result["error"] is True
        assert "file_id" in result["message"]
        assert "title" in result["message"]

    def test_empty_string_file_id(self) -> None:
        result = do_rename(file_id="", title="New Name")
        assert result["error"] is True

    def test_empty_string_title(self) -> None:
        result = do_rename(file_id="f1", title="")
        assert result["error"] is True

    def test_rejects_bad_file_id(self) -> None:
        result = do_rename(file_id="bad id!", title="New Name")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"

    def test_control_chars_stripped_from_title(self) -> None:
        result = do_rename(file_id="abc123", title="\x00\x01\x02")
        assert result["error"] is True
        assert "empty after removing" in result["message"]


class TestDoRenameSuccess:
    """Successful rename calls."""

    @patch("retry.time.sleep")
    @patch("tools.rename.get_drive_service")
    def test_renames_file(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().update().execute.return_value = {
            "id": "f1",
            "name": "Final Report",
            "webViewLink": "https://docs.google.com/document/d/f1/edit",
        }

        result = do_rename("f1", "Final Report")

        assert isinstance(result, DoResult)
        assert result.operation == "rename"
        assert result.title == "Final Report"
        assert result.file_id == "f1"
        assert result.cues["action"] == "Renamed to 'Final Report'"

    @patch("retry.time.sleep")
    @patch("tools.rename.get_drive_service")
    def test_passes_correct_api_params(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().update().execute.return_value = {
            "id": "f1", "name": "New", "webViewLink": "",
        }

        do_rename("f1", "New")

        # Find the call with keyword args (skip the setup call from return_value wiring)
        calls = mock_service.files().update.call_args_list
        real_calls = [c for c in calls if c != ((), {})]
        assert len(real_calls) == 1
        assert real_calls[0] == ((), {
            "fileId": "f1",
            "body": {"name": "New"},
            "fields": "id,name,webViewLink",
            "supportsAllDrives": True,
        })


class TestDoRenameErrors:
    """Error handling for rename."""

    @patch("retry.time.sleep")
    @patch("tools.rename.get_drive_service")
    def test_mise_error_returns_error_dict(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().update().execute.side_effect = MiseError(
            ErrorKind.NOT_FOUND, "File not found"
        )

        result = do_rename("f1", "New")

        assert result["error"] is True
        assert result["kind"] == "not_found"
        assert "File not found" in result["message"]
