"""Unit tests for Drive conversion adapter — context manager and cleanup."""

from unittest.mock import patch, MagicMock, call

from adapters.conversion import drive_temp_file, cleanup_orphaned_temp_files


class TestDriveTempFile:
    """Tests for drive_temp_file context manager."""

    @patch("adapters.conversion.delete_temp_file")
    @patch("adapters.conversion.upload_and_convert")
    def test_yields_temp_id_and_cleans_up(
        self, mock_upload: MagicMock, mock_delete: MagicMock
    ) -> None:
        """Normal path: yields temp ID, deletes on exit."""
        mock_upload.return_value = "temp_file_abc"
        mock_delete.return_value = True

        with drive_temp_file(file_bytes=b"data", source_mime="application/pdf") as temp_id:
            assert temp_id == "temp_file_abc"

        mock_delete.assert_called_once_with("temp_file_abc", "_mise_temp_")

    @patch("adapters.conversion.delete_temp_file")
    @patch("adapters.conversion.upload_and_convert")
    def test_cleans_up_on_exception(
        self, mock_upload: MagicMock, mock_delete: MagicMock
    ) -> None:
        """Cleanup runs even when body raises."""
        mock_upload.return_value = "temp_file_xyz"
        mock_delete.return_value = True

        try:
            with drive_temp_file(file_bytes=b"data", source_mime="text/plain") as temp_id:
                raise ValueError("something went wrong")
        except ValueError:
            pass

        mock_delete.assert_called_once_with("temp_file_xyz", "_mise_temp_")

    @patch("adapters.conversion.logger")
    @patch("adapters.conversion.delete_temp_file")
    @patch("adapters.conversion.upload_and_convert")
    def test_logs_warning_when_delete_fails(
        self, mock_upload: MagicMock, mock_delete: MagicMock, mock_logger: MagicMock
    ) -> None:
        """When delete returns False, logs orphan warning."""
        mock_upload.return_value = "temp_file_orphan"
        mock_delete.return_value = False

        with drive_temp_file(
            file_bytes=b"data", source_mime="application/pdf", file_id_hint="abc123"
        ) as temp_id:
            assert temp_id == "temp_file_orphan"

        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "Orphaned" in warning_msg
        assert "temp_file_orphan" in warning_msg

    @patch("adapters.conversion.delete_temp_file")
    @patch("adapters.conversion.upload_and_convert")
    def test_temp_name_includes_hint(
        self, mock_upload: MagicMock, mock_delete: MagicMock
    ) -> None:
        """file_id_hint is incorporated into the temp name passed to delete."""
        mock_upload.return_value = "temp_123"
        mock_delete.return_value = True

        with drive_temp_file(
            file_bytes=b"x", source_mime="text/plain", file_id_hint="myhint"
        ):
            pass

        mock_delete.assert_called_once_with("temp_123", "_mise_temp_myhint")


class TestCleanupOrphanedTempFiles:
    """Tests for cleanup_orphaned_temp_files."""

    @patch("adapters.conversion.get_drive_service")
    def test_deletes_found_files(self, mock_get_service: MagicMock) -> None:
        """Finds orphans and deletes them, returns count."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        mock_service.files().list().execute.return_value = {
            "files": [
                {"id": "f1", "name": "_mise_temp_abc", "createdTime": "2026-01-01T00:00:00Z"},
                {"id": "f2", "name": "_mise_temp_xyz", "createdTime": "2026-01-01T00:00:00Z"},
            ]
        }

        count = cleanup_orphaned_temp_files()

        assert count == 2
        delete_calls = mock_service.files().delete.call_args_list
        file_ids = {c.kwargs["fileId"] for c in delete_calls}
        assert file_ids == {"f1", "f2"}

    @patch("adapters.conversion.get_drive_service")
    def test_returns_zero_when_no_orphans(self, mock_get_service: MagicMock) -> None:
        """No orphans found, returns 0."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().list().execute.return_value = {"files": []}

        count = cleanup_orphaned_temp_files()

        assert count == 0

    @patch("adapters.conversion.logger")
    @patch("adapters.conversion.get_drive_service")
    def test_partial_delete_failure(
        self, mock_get_service: MagicMock, mock_logger: MagicMock
    ) -> None:
        """Continues deleting even when some deletions fail."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        mock_service.files().list().execute.return_value = {
            "files": [
                {"id": "f1", "name": "_mise_temp_a", "createdTime": "2026-01-01T00:00:00Z"},
                {"id": "f2", "name": "_mise_temp_b", "createdTime": "2026-01-01T00:00:00Z"},
            ]
        }

        # First delete succeeds, second fails
        execute_mock = MagicMock()
        execute_mock.side_effect = [None, Exception("API error")]
        mock_service.files().delete().execute = execute_mock

        count = cleanup_orphaned_temp_files()

        assert count == 1  # Only one succeeded

    @patch("adapters.conversion.logger")
    @patch("adapters.conversion.get_drive_service")
    def test_list_failure_returns_zero(
        self, mock_get_service: MagicMock, mock_logger: MagicMock
    ) -> None:
        """If listing files fails, returns 0 and logs warning."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().list().execute.side_effect = Exception("Network error")

        count = cleanup_orphaned_temp_files()

        assert count == 0
        mock_logger.warning.assert_called_once()
        assert "Orphan cleanup failed" in mock_logger.warning.call_args[0][0]
