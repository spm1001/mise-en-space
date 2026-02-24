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
    @patch("adapters.gmail.get_gmail_service")
    def test_user_label_resolved(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.users().labels().list().execute.return_value = {
            "labels": [
                {"id": "Label_1", "name": "Projects/Active", "type": "user"},
                {"id": "Label_2", "name": "Follow-up", "type": "user"},
            ],
        }

        result = resolve_label_name("Projects/Active")
        assert result == "Label_1"

    @patch("retry.time.sleep")
    @patch("adapters.gmail.get_gmail_service")
    def test_user_label_case_insensitive(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.users().labels().list().execute.return_value = {
            "labels": [
                {"id": "Label_1", "name": "Projects/Active", "type": "user"},
            ],
        }

        result = resolve_label_name("projects/active")
        assert result == "Label_1"

    @patch("retry.time.sleep")
    @patch("adapters.gmail.get_gmail_service")
    def test_not_found_raises(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.users().labels().list().execute.return_value = {
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
    @patch("adapters.gmail.get_gmail_service")
    def test_add_labels(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.users().threads().modify().execute.return_value = {}

        result = modify_thread("thread_1", add_label_ids=["STARRED"])

        assert isinstance(result, ModifyThreadResult)
        assert result.thread_id == "thread_1"
        assert result.added_labels == ["STARRED"]
        assert result.removed_labels == []

    @patch("retry.time.sleep")
    @patch("adapters.gmail.get_gmail_service")
    def test_remove_labels(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.users().threads().modify().execute.return_value = {}

        result = modify_thread("thread_1", remove_label_ids=["INBOX"])

        assert result.removed_labels == ["INBOX"]
        assert result.added_labels == []

    @patch("retry.time.sleep")
    @patch("adapters.gmail.get_gmail_service")
    def test_add_and_remove(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.users().threads().modify().execute.return_value = {}

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
            thread_id="t1", added_labels=[], removed_labels=["INBOX"],
        )

        result = do_archive(file_id="t1")

        assert isinstance(result, DoResult)
        assert result.operation == "archive"
        assert result.file_id == "t1"
        assert "archived" in result.cues["action"].lower()

        # Verify modify was called to remove INBOX
        mock_modify.assert_called_once_with(
            thread_id="t1", remove_label_ids=["INBOX"],
        )

    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    def test_api_error(self, mock_modify, _sleep) -> None:
        mock_modify.side_effect = MiseError(ErrorKind.NOT_FOUND, "Thread not found")

        result = do_archive(file_id="bad_thread")
        assert result["error"] is True
        assert result["kind"] == "not_found"


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
            thread_id="t1", added_labels=["STARRED"], removed_labels=[],
        )

        result = do_star(file_id="t1")

        assert isinstance(result, DoResult)
        assert result.operation == "star"
        assert "starred" in result.cues["action"].lower()

        mock_modify.assert_called_once_with(
            thread_id="t1", add_label_ids=["STARRED"],
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
        result = do_label(file_id="t1")
        assert result["error"] is True
        assert "label" in result["message"]


class TestDoLabelSuccess:
    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    @patch("tools.gmail_ops.resolve_label_name")
    def test_adds_label(self, mock_resolve, mock_modify, _sleep) -> None:
        mock_resolve.return_value = "Label_1"
        mock_modify.return_value = ModifyThreadResult(
            thread_id="t1", added_labels=["Label_1"], removed_labels=[],
        )

        result = do_label(file_id="t1", label="Projects/Active")

        assert isinstance(result, DoResult)
        assert result.operation == "label"
        assert result.cues["label"] == "Projects/Active"
        assert result.cues["removed"] is False
        assert "added" in result.cues["action"].lower()

        mock_resolve.assert_called_once_with("Projects/Active")
        mock_modify.assert_called_once_with(
            thread_id="t1", add_label_ids=["Label_1"],
        )

    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    @patch("tools.gmail_ops.resolve_label_name")
    def test_removes_label(self, mock_resolve, mock_modify, _sleep) -> None:
        mock_resolve.return_value = "Label_2"
        mock_modify.return_value = ModifyThreadResult(
            thread_id="t1", added_labels=[], removed_labels=["Label_2"],
        )

        result = do_label(file_id="t1", label="Follow-up", remove=True)

        assert isinstance(result, DoResult)
        assert result.cues["removed"] is True
        assert "removed" in result.cues["action"].lower()

        mock_modify.assert_called_once_with(
            thread_id="t1", remove_label_ids=["Label_2"],
        )

    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.resolve_label_name")
    def test_label_not_found(self, mock_resolve, _sleep) -> None:
        mock_resolve.side_effect = MiseError(ErrorKind.NOT_FOUND, "Label 'Bogus' not found")

        result = do_label(file_id="t1", label="Bogus")

        assert result["error"] is True
        assert result["kind"] == "not_found"
        assert "Bogus" in result["message"]

    @patch("retry.time.sleep")
    @patch("tools.gmail_ops.modify_thread")
    @patch("tools.gmail_ops.resolve_label_name")
    def test_modify_failure(self, mock_resolve, mock_modify, _sleep) -> None:
        mock_resolve.return_value = "Label_1"
        mock_modify.side_effect = MiseError(ErrorKind.PERMISSION_DENIED, "No access")

        result = do_label(file_id="t1", label="Projects")

        assert result["error"] is True
        assert result["kind"] == "permission_denied"