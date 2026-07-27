"""Tests for do(copy) — mise-hezuke.

The verb the gather-into-one-folder workflow needed: evidence packs, board
packs, share-a-snapshot jobs. Batch is the common case, and a copy-restricted
source is a permission fact rather than a transport failure, so the two are
counted apart.
"""

from unittest.mock import MagicMock, patch

import pytest

from models import DoResult
from tools.copy import do_copy

_FOLDER_MIME = "application/vnd.google-apps.folder"


def _client(*, can_copy: bool = True, source_mime: str = "application/pdf") -> MagicMock:
    c = MagicMock()

    def get_json(url: str, **kw):
        if url.endswith("/destfolder"):
            return {"mimeType": _FOLDER_MIME, "name": "Evidence Pack"}
        return {
            "id": "src1", "name": "Report.pdf", "mimeType": source_mime,
            "capabilities": {"canCopy": can_copy},
        }

    c.get_json.side_effect = get_json
    c.post_json.return_value = {
        "id": "copy1", "name": "Report.pdf",
        "webViewLink": "https://drive.google.com/file/d/copy1/view",
    }
    return c


class TestSingleCopy:
    @patch("retry.time.sleep")
    @patch("tools.copy.get_sync_client")
    def test_copies_into_folder(self, mock_get, _sleep) -> None:
        mock_get.return_value = _client()
        result = do_copy(file_id="src1", folder_id="destfolder")

        assert isinstance(result, DoResult)
        assert result.file_id == "copy1"
        assert result.operation == "copy"
        # Provenance both ways — a copy strips it, and the index is the deliverable.
        assert result.cues["source_id"] == "src1"
        assert result.cues["copy_id"] == "copy1"
        assert result.cues["destination_folder"] == "Evidence Pack"

    @patch("retry.time.sleep")
    @patch("tools.copy.get_sync_client")
    def test_folder_id_is_optional(self, mock_get, _sleep) -> None:
        """Drive's own default is beside the original; don't invent a requirement."""
        client = _client()
        mock_get.return_value = client
        result = do_copy(file_id="src1")

        assert isinstance(result, DoResult)
        body = client.post_json.call_args.kwargs["json_body"]
        assert "parents" not in body

    @patch("retry.time.sleep")
    @patch("tools.copy.get_sync_client")
    def test_title_renames_the_copy(self, mock_get, _sleep) -> None:
        client = _client()
        mock_get.return_value = client
        do_copy(file_id="src1", folder_id="destfolder", title="01 — Evidence")

        assert client.post_json.call_args.kwargs["json_body"]["name"] == "01 — Evidence"

    @patch("retry.time.sleep")
    @patch("tools.copy.get_sync_client")
    def test_can_copy_false_is_blocked_not_a_raw_http_error(self, mock_get, _sleep) -> None:
        """Third-party PDFs can carry a copy restriction. One pre-flight GET buys
        a message naming the cause instead of an opaque POST failure."""
        client = _client(can_copy=False)
        mock_get.return_value = client
        result = do_copy(file_id="src1", folder_id="destfolder")

        assert result["error"] is True
        assert result["kind"] == "permission_denied"
        assert "restricted copying" in result["message"]
        client.post_json.assert_not_called()   # never attempted

    @patch("retry.time.sleep")
    @patch("tools.copy.get_sync_client")
    def test_folder_source_is_refused(self, mock_get, _sleep) -> None:
        """files.copy does not recurse — copying a folder yields an empty folder,
        which looks like success and loses everything inside."""
        mock_get.return_value = _client(source_mime=_FOLDER_MIME)
        result = do_copy(file_id="src1", folder_id="destfolder")

        assert result["error"] is True
        assert "does not recurse" in result["message"]

    def test_requires_file_id(self) -> None:
        result = do_copy(folder_id="destfolder")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"

    def test_rejects_malformed_folder_id(self) -> None:
        result = do_copy(file_id="src1", folder_id="abc' OR '1'='1")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"


class TestBatchCopy:
    @patch("retry.time.sleep")
    @patch("tools.copy.get_sync_client")
    def test_batch_returns_source_to_copy_mapping(self, mock_get, _sleep) -> None:
        """The mapping IS the feature — the gather job exists to build an index."""
        mock_get.return_value = _client()
        result = do_copy(file_id=["src1", "src2", "src3"], folder_id="destfolder")

        assert result["batch"] is True
        assert result["total"] == 3
        assert result["succeeded"] == 3
        assert all("source_id" in r and "copy_id" in r for r in result["results"])

    @patch("retry.time.sleep")
    @patch("tools.copy.get_sync_client")
    def test_blocked_counted_apart_from_failed(self, mock_get, _sleep) -> None:
        """A restricted file and a broken API need different responses from the
        caller — ask the owner, versus retry."""
        mock_get.return_value = _client(can_copy=False)
        result = do_copy(file_id=["src1", "src2"], folder_id="destfolder")

        assert result["blocked"] == 2
        assert result["failed"] == 0
        assert all(r["blocked"] for r in result["results"])

    @patch("retry.time.sleep")
    @patch("tools.copy.get_sync_client")
    def test_bad_destination_fails_before_anything_is_duplicated(self, mock_get, _sleep) -> None:
        """Checked once, up front — a wrong folder discovered halfway leaves
        orphan copies scattered where nobody looks for them."""
        client = MagicMock()
        client.get_json.return_value = {"mimeType": "application/pdf", "name": "Not A Folder"}
        mock_get.return_value = client

        result = do_copy(file_id=["src1", "src2"], folder_id="destfolder")

        assert result["error"] is True
        assert "is not a folder" in result["message"]
        client.post_json.assert_not_called()

    def test_title_with_batch_is_refused(self) -> None:
        """One name across N files produces N identically-named copies."""
        result = do_copy(file_id=["a", "b"], folder_id="destfolder", title="Same Name")
        assert result["error"] is True
        assert "single copy" in result["message"]


class TestRegistration:
    def test_copy_is_wired_into_dispatch(self) -> None:
        from tools import OPERATIONS
        from tools.dispatch import DISPATCH, REQUIRED_PARAMS
        assert "copy" in OPERATIONS
        assert "copy" in DISPATCH
        assert REQUIRED_PARAMS["copy"] == {"file_id"}

    def test_copy_is_not_remote_allowed(self) -> None:
        """Deliberate. The remote whitelist was audited Mar 2026; copy creates
        Drive files under the single shared credential and has not been through
        that audit. Add it consciously, not as a side effect of adding the op."""
        from tools.remote import REMOTE_ALLOWED_OPS
        assert "copy" not in REMOTE_ALLOWED_OPS
