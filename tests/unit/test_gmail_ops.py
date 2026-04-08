"""Tests for Gmail thread operations (archive, label, star)."""

from unittest.mock import patch, MagicMock

import pytest

from adapters.gmail import (
    ModifyThreadResult,
    SYSTEM_LABELS,
    resolve_label_name,
    modify_thread,
)
from models import DoResult, MiseError, ErrorKind
from tools.gmail_ops import (
    _gmail_thread_link,
    do_archive,
    do_star,
    do_label,
)


# =============================================================================
# ADAPTER: Label resolution
# =============================================================================


class TestSystemLabels:
    def test_inbox(self) -> None:
        assert SYSTEM_LABELS["INBOX"] == "INBOX"

    def test_starred(self) -> None:
        assert SYSTEM_LABELS["STARRED"] == "STARRED"


class TestResolveLabelName:
    """resolve_label_name maps names to IDs."""

    def test_system_label_case_insensitive(self) -> None:
        # System labels don't need API call
        assert resolve_label_name("INBOX") == "INBOX"
        assert resolve_label_name("inbox") == "INBOX"
        assert resolve_label_name("Starred") == "STARRED"

    @patch("retry.time.sleep")
    @patch("adapters.gmail.get_sync_client")
    def test_user_label_resolved(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "labels": [
                {"id": "Label_1", "name": "Projects/Active", "type": "user"},
                {"id": "Label_2", "name": "Follow-up", "type": "user"},
            ],
        }

        result = resolve_label_name("Projects/Active")
        assert result == "Label_1"

    @patch("retry.time.sleep")
    @patch("adapters.gmail.get_sync_client")
    def test_user_label_case_insensitive(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "labels": [
                {"id": "Label_1", "name": "Projects/Active", "type": "user"},
            ],
        }

        result = resolve_label_name("projects/active")
        assert result == "Label_1"

    @patch("retry.time.sleep")
    @patch("adapters.gmail.get_sync_client")
    def test_not_found_raises(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "labels": [
                {"id": "Label_1", "name": "Existing", "type": "user"},
            ],
        }

        with pytest.raises(MiseError) as exc_info:
            resolve_label_name("Nonexistent")

        assert exc_info.value.kind == ErrorKind.NOT_FOUND
        assert "Nonexistent" in exc_info.value.message


class TestModifyThread:
    """modify_thread calls the Gmail API correctly."""

    @patch("retry.time.sleep")
    @patch("adapters.gmail.get_sync_client")
    def test_add_labels(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.post_json.return_value = {}

        result = modify_thread("thread_1", add_label_ids=["STARRED"])

        assert isinstance(result, ModifyThreadResult)
        assert result.thread_id == "thread_1"
        assert result.added_labels == ["STARRED"]
        assert result.removed_labels == []

    @patch("retry.time.sleep")
    @patch("adapters.gmail.get_sync_client")
    def test_remove_labels(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.post_json.return_value = {}

        result = modify_thread("thread_1", remove_label_ids=["INBOX"])

        assert result.removed_labels == ["INBOX"]
        assert result.added_labels == []

    @patch("retry.time.sleep")
    @patch("adapters.gmail.get_sync_client")
    def test_add_and_remove(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.post_json.return_value = {}

        result = modify_thread(
            "thread_1",
            add_label_ids=["Label_1"],
            remove_label_ids=["INBOX"],
        )

        assert result.added_labels == ["Label_1"]
        assert result.removed_labels == ["INBOX"]


# =============================================================================
# TOOL: Thread link
# =============================================================================


class TestGmailThreadLink:
    def test_format(self) -> None:
        assert _gmail_thread_link("abc123") == "https://mail.google.com/mail/#all/abc123"


# =============================================================================
# TOOL: do_archive
# =============================================================================


class TestDoArchiveValidation:
    def test_missing_file_id(self) -> None:
        result = do_archive()
        assert result["error"] is True
        assert "file_id" in result["message"]


class TestDoArchiveSuccess:
    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    def test_archives_thread(self, mock_modify, _sleep) -> None:
        mock_modify.return_value = ModifyThreadResult(
            thread_id="a1b2c3d4e5f6a7b8", added_labels=[], removed_labels=["INBOX"],
        )

        result = do_archive(file_id="a1b2c3d4e5f6a7b8")

        assert isinstance(result, DoResult)
        assert result.operation == "archive"
        assert result.file_id == "a1b2c3d4e5f6a7b8"
        assert "archived" in result.cues["action"].lower()

        # Verify modify was called to remove INBOX
        mock_modify.assert_called_once_with(
            thread_id="a1b2c3d4e5f6a7b8", remove_label_ids=["INBOX"],
        )

    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    def test_api_error(self, mock_modify, _sleep) -> None:
        mock_modify.side_effect = MiseError(ErrorKind.NOT_FOUND, "Thread not found")

        result = do_archive(file_id="abc123def456abc1")
        assert result["error"] is True
        assert result["kind"] == "not_found"

    def test_rejects_malformed_thread_id(self) -> None:
        result = do_archive(file_id="bad_thread!")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"


# =============================================================================
# TOOL: do_star
# =============================================================================


class TestDoStarValidation:
    def test_missing_file_id(self) -> None:
        result = do_star()
        assert result["error"] is True
        assert "file_id" in result["message"]


class TestDoStarSuccess:
    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    def test_stars_thread(self, mock_modify, _sleep) -> None:
        mock_modify.return_value = ModifyThreadResult(
            thread_id="a1b2c3d4e5f6a7b8", added_labels=["STARRED"], removed_labels=[],
        )

        result = do_star(file_id="a1b2c3d4e5f6a7b8")

        assert isinstance(result, DoResult)
        assert result.operation == "star"
        assert "starred" in result.cues["action"].lower()

        mock_modify.assert_called_once_with(
            thread_id="a1b2c3d4e5f6a7b8", add_label_ids=["STARRED"],
        )


# =============================================================================
# TOOL: do_label
# =============================================================================


class TestDoLabelValidation:
    def test_missing_file_id(self) -> None:
        result = do_label(label="Projects")
        assert result["error"] is True
        assert "file_id" in result["message"]

    def test_missing_label(self) -> None:
        result = do_label(file_id="a1b2c3d4e5f6a7b8")
        assert result["error"] is True
        assert "label" in result["message"]


class TestDoLabelSuccess:
    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    @patch("tools.gmail_ops.resolve_label_name")
    def test_adds_label(self, mock_resolve, mock_modify, _sleep) -> None:
        mock_resolve.return_value = "Label_1"
        mock_modify.return_value = ModifyThreadResult(
            thread_id="a1b2c3d4e5f6a7b8", added_labels=["Label_1"], removed_labels=[],
        )

        result = do_label(file_id="a1b2c3d4e5f6a7b8", label="Projects/Active")

        assert isinstance(result, DoResult)
        assert result.operation == "label"
        assert result.cues["label"] == "Projects/Active"
        assert result.cues["removed"] is False
        assert "added" in result.cues["action"].lower()

        mock_resolve.assert_called_once_with("Projects/Active")
        mock_modify.assert_called_once_with(
            thread_id="a1b2c3d4e5f6a7b8", add_label_ids=["Label_1"],
        )

    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    @patch("tools.gmail_ops.resolve_label_name")
    def test_removes_label(self, mock_resolve, mock_modify, _sleep) -> None:
        mock_resolve.return_value = "Label_2"
        mock_modify.return_value = ModifyThreadResult(
            thread_id="a1b2c3d4e5f6a7b8", added_labels=[], removed_labels=["Label_2"],
        )

        result = do_label(file_id="a1b2c3d4e5f6a7b8", label="Follow-up", remove=True)

        assert isinstance(result, DoResult)
        assert result.cues["removed"] is True
        assert "removed" in result.cues["action"].lower()

        mock_modify.assert_called_once_with(
            thread_id="a1b2c3d4e5f6a7b8", remove_label_ids=["Label_2"],
        )

    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.resolve_label_name")
    def test_label_not_found(self, mock_resolve, _sleep) -> None:
        mock_resolve.side_effect = MiseError(ErrorKind.NOT_FOUND, "Label 'Bogus' not found")

        result = do_label(file_id="a1b2c3d4e5f6a7b8", label="Bogus")

        assert result["error"] is True
        assert result["kind"] == "not_found"
        assert "Bogus" in result["message"]

    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    @patch("tools.gmail_ops.resolve_label_name")
    def test_modify_failure(self, mock_resolve, mock_modify, _sleep) -> None:
        mock_resolve.return_value = "Label_1"
        mock_modify.side_effect = MiseError(ErrorKind.PERMISSION_DENIED, "No access")

        result = do_label(file_id="a1b2c3d4e5f6a7b8", label="Projects")

        assert result["error"] is True
        assert result["kind"] == "permission_denied"


# =============================================================================
# BATCH: do_archive
# =============================================================================


THREAD_A = "a1b2c3d4e5f6a7b8"
THREAD_B = "b2c3d4e5f6a7b8c9"
THREAD_C = "c3d4e5f6a7b8c9d0"


class TestBatchArchive:
    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    def test_batch_all_succeed(self, mock_modify, _sleep) -> None:
        mock_modify.return_value = ModifyThreadResult(
            thread_id="", added_labels=[], removed_labels=["INBOX"],
        )

        result = do_archive(file_id=[THREAD_A, THREAD_B, THREAD_C])

        assert result["batch"] is True
        assert result["operation"] == "archive"
        assert result["total"] == 3
        assert result["succeeded"] == 3
        assert result["failed"] == 0
        assert len(result["results"]) == 3
        assert all(r["ok"] for r in result["results"])

    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    def test_batch_partial_failure(self, mock_modify, _sleep) -> None:
        mock_modify.side_effect = [
            ModifyThreadResult(thread_id=THREAD_A, added_labels=[], removed_labels=["INBOX"]),
            MiseError(ErrorKind.NOT_FOUND, "Thread not found"),
            ModifyThreadResult(thread_id=THREAD_C, added_labels=[], removed_labels=["INBOX"]),
        ]

        result = do_archive(file_id=[THREAD_A, THREAD_B, THREAD_C])

        assert result["succeeded"] == 2
        assert result["failed"] == 1
        assert result["results"][0]["ok"] is True
        assert result["results"][1]["ok"] is False
        assert "not found" in result["results"][1]["error"].lower()
        assert result["results"][2]["ok"] is True

    def test_batch_rejects_invalid_id(self) -> None:
        result = do_archive(file_id=[THREAD_A, "bad!"])

        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "file_id[1]" in result["message"]


# =============================================================================
# BATCH: do_star
# =============================================================================


class TestBatchStar:
    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    def test_batch_all_succeed(self, mock_modify, _sleep) -> None:
        mock_modify.return_value = ModifyThreadResult(
            thread_id="", added_labels=["STARRED"], removed_labels=[],
        )

        result = do_star(file_id=[THREAD_A, THREAD_B])

        assert result["batch"] is True
        assert result["operation"] == "star"
        assert result["total"] == 2
        assert result["succeeded"] == 2
        assert result["failed"] == 0

    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    def test_batch_partial_failure(self, mock_modify, _sleep) -> None:
        mock_modify.side_effect = [
            ModifyThreadResult(thread_id=THREAD_A, added_labels=["STARRED"], removed_labels=[]),
            MiseError(ErrorKind.RATE_LIMITED, "Too many requests"),
        ]

        result = do_star(file_id=[THREAD_A, THREAD_B])

        assert result["succeeded"] == 1
        assert result["failed"] == 1
        assert result["results"][1]["ok"] is False


# =============================================================================
# BATCH: do_label
# =============================================================================


class TestBatchLabel:
    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    @patch("tools.gmail_ops.resolve_label_name")
    def test_batch_add_label(self, mock_resolve, mock_modify, _sleep) -> None:
        mock_resolve.return_value = "Label_1"
        mock_modify.return_value = ModifyThreadResult(
            thread_id="", added_labels=["Label_1"], removed_labels=[],
        )

        result = do_label(
            file_id=[THREAD_A, THREAD_B, THREAD_C],
            label="Projects/Active",
        )

        assert result["batch"] is True
        assert result["operation"] == "label"
        assert result["total"] == 3
        assert result["succeeded"] == 3
        assert result["label"] == "Projects/Active"
        assert result["removed"] is False
        # Label resolved once, not per thread
        mock_resolve.assert_called_once_with("Projects/Active")

    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    @patch("tools.gmail_ops.resolve_label_name")
    def test_batch_remove_label(self, mock_resolve, mock_modify, _sleep) -> None:
        mock_resolve.return_value = "UNREAD"
        mock_modify.return_value = ModifyThreadResult(
            thread_id="", added_labels=[], removed_labels=["UNREAD"],
        )

        result = do_label(
            file_id=[THREAD_A, THREAD_B],
            label="UNREAD",
            remove=True,
        )

        assert result["batch"] is True
        assert result["succeeded"] == 2
        assert result["removed"] is True

    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.resolve_label_name")
    def test_batch_label_not_found(self, mock_resolve, _sleep) -> None:
        mock_resolve.side_effect = MiseError(ErrorKind.NOT_FOUND, "Label 'Bogus' not found")

        result = do_label(file_id=[THREAD_A], label="Bogus")

        # Fails before any thread processing
        assert result["error"] is True
        assert result["kind"] == "not_found"

    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    @patch("tools.gmail_ops.resolve_label_name")
    def test_batch_partial_failure(self, mock_resolve, mock_modify, _sleep) -> None:
        mock_resolve.return_value = "Label_1"
        mock_modify.side_effect = [
            ModifyThreadResult(thread_id=THREAD_A, added_labels=["Label_1"], removed_labels=[]),
            MiseError(ErrorKind.PERMISSION_DENIED, "No access"),
        ]

        result = do_label(
            file_id=[THREAD_A, THREAD_B],
            label="Projects",
        )

        assert result["succeeded"] == 1
        assert result["failed"] == 1
        assert result["results"][0]["ok"] is True
        assert result["results"][1]["ok"] is False