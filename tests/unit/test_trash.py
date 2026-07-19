"""Tests for the trash operation — Drive trash + Gmail draft discard."""

from unittest.mock import patch

import pytest

from models import DoResult, ErrorKind, MiseError
from tools.trash import _is_draft_id, do_trash


class TestDraftIdRouting:
    """Draft IDs (r + digits, from the drafts API) route deterministically."""

    def test_observed_shapes_match(self) -> None:
        # Real shapes from the call log (2026-07-19)
        assert _is_draft_id("r7776227818802419254")
        assert _is_draft_id("r-3604756222077779159")

    def test_drive_ids_do_not_match(self) -> None:
        assert not _is_draft_id("1WlPeFLRVI84ArVJexRwU32hSkqT4anF0-XUniYa5HJk")
        assert not _is_draft_id("rabc123")  # letters after r — not a draft id


class TestTrashSingle:
    @patch("tools.trash.delete_draft")
    def test_draft_id_discards_draft(self, mock_delete) -> None:
        result = do_trash(file_id="r7776227818802419254")

        mock_delete.assert_called_once_with("r7776227818802419254")
        assert isinstance(result, DoResult)
        assert result.operation == "trash"
        assert "permanent" in result.cues["action"].lower()

    @patch("tools.trash._trash_drive_file")
    def test_drive_id_moves_to_trash(self, mock_trash) -> None:
        mock_trash.return_value = {
            "id": "1WlPeFLRVI84ArVJexRwU32hSkqT4anF0X",
            "name": "old form",
            "webViewLink": "https://docs.google.com/forms/d/x/edit",
        }

        result = do_trash(file_id="1WlPeFLRVI84ArVJexRwU32hSkqT4anF0X")

        mock_trash.assert_called_once()
        assert isinstance(result, DoResult)
        assert result.title == "old form"
        assert "recoverable" in result.cues["action"].lower()

    def test_missing_file_id_errors(self) -> None:
        result = do_trash()
        assert result["error"] is True

    def test_malformed_id_errors(self) -> None:
        result = do_trash(file_id="../etc/passwd")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"


class TestTrashBatch:
    @patch("tools.trash.delete_draft")
    @patch("tools.trash._trash_drive_file")
    def test_mixed_batch_routes_each(self, mock_drive, mock_draft) -> None:
        mock_drive.return_value = {"id": "x", "name": "doc", "webViewLink": "w"}

        result = do_trash(file_id=[
            "1WlPeFLRVI84ArVJexRwU32hSkqT4anF0X",
            "r7776227818802419254",
        ])

        assert result["batch"] is True
        assert result["succeeded"] == 2
        assert result["failed"] == 0
        kinds = [r["result"] for r in result["results"]]
        assert kinds == ["drive_trashed", "draft_discarded"]

    @patch("tools.trash.delete_draft")
    @patch("tools.trash._trash_drive_file")
    def test_partial_failure_reported_per_item(self, mock_drive, mock_draft) -> None:
        mock_drive.side_effect = MiseError(ErrorKind.NOT_FOUND, "gone already")

        result = do_trash(file_id=[
            "1WlPeFLRVI84ArVJexRwU32hSkqT4anF0X",
            "r7776227818802419254",
        ])

        assert result["succeeded"] == 1
        assert result["failed"] == 1
        assert result["results"][0]["ok"] is False
        assert "gone already" in result["results"][0]["error"]

    def test_batch_validates_all_ids_first(self) -> None:
        result = do_trash(file_id=["1WlPeFLRVI84ArVJexRwU32hSkqT4anF0X", "bad id!"])
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
