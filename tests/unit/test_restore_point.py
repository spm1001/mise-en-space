"""
Tests for the pre-edit restore point (mise-cizuzi).

Covers the adapter (get_head_revision pagination), the capture helper
(anchor / comment / best-effort failure), the cues merge, and the wiring
into the four mutating Doc ops.
"""

import pytest
from unittest.mock import patch, MagicMock

from models import DoResult, MiseError
from adapters.drive import get_head_revision
from tools.restore_point import capture_restore_point, merge_restore_cues


# ============================================================================
# ADAPTER — get_head_revision
# ============================================================================

class TestGetHeadRevision:
    @patch("adapters.drive.get_sync_client")
    def test_single_page_returns_last(self, mock_get_client) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "revisions": [
                {"id": "1", "modifiedTime": "2026-07-22T18:58:17.560Z"},
                {"id": "52", "modifiedTime": "2026-07-22T18:59:12.794Z"},
            ]
        }
        with patch("retry.time.sleep"):
            rev = get_head_revision("doc1")
        assert rev == {"id": "52", "modifiedTime": "2026-07-22T18:59:12.794Z"}

    @patch("adapters.drive.get_sync_client")
    def test_follows_pagination_to_last_page(self, mock_get_client) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.side_effect = [
            {"revisions": [{"id": "1", "modifiedTime": "t1"}], "nextPageToken": "tok"},
            {"revisions": [{"id": "99", "modifiedTime": "t99"}]},
        ]
        with patch("retry.time.sleep"):
            rev = get_head_revision("doc1")
        assert rev["id"] == "99"
        assert mock_client.get_json.call_count == 2
        assert mock_client.get_json.call_args_list[1].kwargs["params"]["pageToken"] == "tok"

    @patch("adapters.drive.get_sync_client")
    def test_no_revisions_raises_not_found(self, mock_get_client) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {"revisions": []}
        with patch("retry.time.sleep"):
            with pytest.raises(MiseError, match="no readable revisions"):
                get_head_revision("doc1")


# ============================================================================
# CAPTURE HELPER
# ============================================================================

_REV = {"id": "52", "modifiedTime": "2026-07-22T18:59:12.794Z"}


class TestCaptureRestorePoint:
    @patch("tools.restore_point.create_comment")
    @patch("tools.restore_point.get_head_revision", return_value=_REV)
    def test_anchor_only(self, mock_rev, mock_comment) -> None:
        cues = capture_restore_point("doc1")
        assert cues["restore_point"] == {
            "revision_id": "52", "modified_time": "2026-07-22T18:59:12.794Z",
        }
        mock_comment.assert_not_called()
        assert "warnings" not in cues

    @patch("tools.restore_point.create_comment")
    @patch("tools.restore_point.get_head_revision", return_value=_REV)
    def test_comment_posted_with_anchor_details(self, mock_rev, mock_comment) -> None:
        mock_comment.return_value = MagicMock(id="cmt123")
        cues = capture_restore_point("doc1", comment=True)
        assert cues["restore_point_comment"] == "cmt123"
        text = mock_comment.call_args.args[1]
        assert text.startswith("[agent] ")
        assert "revision 52" in text
        assert "Version history" in text

    @patch("tools.restore_point.create_comment")
    @patch("tools.restore_point.get_head_revision", side_effect=RuntimeError("api down"))
    def test_anchor_failure_warns_never_raises(self, mock_rev, mock_comment) -> None:
        cues = capture_restore_point("doc1", comment=True)
        assert "restore_point" not in cues
        assert any("Restore point unavailable" in w for w in cues["warnings"])
        mock_comment.assert_not_called()  # no anchor → nothing to mark

    @patch("tools.restore_point.create_comment", side_effect=RuntimeError("403"))
    @patch("tools.restore_point.get_head_revision", return_value=_REV)
    def test_comment_failure_keeps_anchor(self, mock_rev, mock_comment) -> None:
        cues = capture_restore_point("doc1", comment=True)
        assert cues["restore_point"]["revision_id"] == "52"
        assert any("comment could not be posted" in w for w in cues["warnings"])


class TestMergeRestoreCues:
    def test_merges_into_do_result(self) -> None:
        result = DoResult(file_id="f", title="t", web_link="w",
                          operation="overwrite", cues={"char_count": 5})
        merged = merge_restore_cues(result, {
            "restore_point": {"revision_id": "52", "modified_time": "t"},
            "warnings": ["partial"],
        })
        assert merged.cues["restore_point"]["revision_id"] == "52"
        assert merged.cues["char_count"] == 5
        assert merged.cues["warnings"] == ["partial"]

    def test_extends_existing_warnings(self) -> None:
        result = DoResult(file_id="f", title="t", web_link="w",
                          operation="overwrite", cues={"warnings": ["old"]})
        merged = merge_restore_cues(result, {"warnings": ["new"]})
        assert merged.cues["warnings"] == ["old", "new"]

    def test_error_dict_passes_through(self) -> None:
        err = {"error": True, "kind": "not_found", "message": "gone"}
        assert merge_restore_cues(err, {"restore_point": {}}) is err


# ============================================================================
# WIRING — the four mutating Doc ops
# ============================================================================

def _doc_meta() -> dict:
    return {"mimeType": "application/vnd.google-apps.document", "name": "My Doc"}


class TestOverwriteWiring:
    @patch("retry.time.sleep")
    @patch("tools.overwrite.upload_file_content", return_value={"name": "My Doc"})
    @patch("tools.overwrite.capture_restore_point", return_value={"restore_point": {"revision_id": "52", "modified_time": "t"}})
    def test_doc_overwrite_captures_with_comment(self, mock_capture, mock_upload, _sleep) -> None:
        from tools.overwrite import do_overwrite
        result = do_overwrite(file_id="doc1", content="new", metadata=_doc_meta())
        mock_capture.assert_called_once_with("doc1", comment=True)
        assert result.cues["restore_point"]["revision_id"] == "52"

    @patch("retry.time.sleep")
    @patch("tools.overwrite.upload_file_content", return_value={"name": "My Doc"})
    @patch("tools.overwrite.capture_restore_point", return_value={})
    def test_restore_comment_false_suppresses_comment(self, mock_capture, mock_upload, _sleep) -> None:
        from tools.overwrite import do_overwrite
        do_overwrite(file_id="doc1", content="new", metadata=_doc_meta(), restore_comment=False)
        mock_capture.assert_called_once_with("doc1", comment=False)

    @patch("tools.overwrite.sheet_overwrite", return_value={"ok": True})
    @patch("tools.overwrite.capture_restore_point")
    def test_sheet_overwrite_skips_capture(self, mock_capture, mock_sheet) -> None:
        from tools.overwrite import do_overwrite
        do_overwrite(file_id="sheet1", content="a,b",
                     metadata={"mimeType": "application/vnd.google-apps.spreadsheet"})
        mock_capture.assert_not_called()


class TestEditWiring:
    def _docs_client(self) -> MagicMock:
        client = MagicMock()
        client.get_json.return_value = {
            "title": "My Doc",
            "body": {"content": [{"endIndex": 10}]},
        }
        client.post_json.return_value = {"replies": [{}]}
        return client

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    @patch("tools.edit.capture_restore_point", return_value={"restore_point": {"revision_id": "7", "modified_time": "t"}})
    def test_append_carries_anchor_no_comment(self, mock_capture, mock_get_client, _sleep) -> None:
        from tools.edit import do_append
        mock_get_client.return_value = self._docs_client()
        result = do_append(file_id="doc1", content="more", metadata=_doc_meta())
        mock_capture.assert_called_once_with("doc1")
        assert result.cues["restore_point"]["revision_id"] == "7"

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    @patch("tools.edit.capture_restore_point", return_value={})
    def test_prepend_and_replace_capture(self, mock_capture, mock_get_client, _sleep) -> None:
        from tools.edit import do_prepend, do_replace_text
        mock_get_client.return_value = self._docs_client()
        do_prepend(file_id="doc1", content="x", metadata=_doc_meta())
        do_replace_text(file_id="doc1", find="a", content="b", metadata=_doc_meta())
        assert mock_capture.call_count == 2

    @patch("tools.edit.plain_append", return_value={"ok": True})
    @patch("tools.edit.capture_restore_point")
    def test_plain_file_append_skips_capture(self, mock_capture, mock_plain) -> None:
        from tools.edit import do_append
        do_append(file_id="f1", content="x", metadata={"mimeType": "text/markdown"})
        mock_capture.assert_not_called()
