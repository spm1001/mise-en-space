"""
Docs smart chips — whole-line @url → insertRichLink (mise-rafote).

The API contract these tests pin was bought with live probes (2026-08-09):
insertRichLink takes richLinkProperties.uri ONLY (a supplied title is
rejected: "Insert rich link requests should not specify a title"), non-Drive
URLs are rejected ("The URL is invalid"), and batchUpdate is atomic — one bad
chip fails the whole pass, hence the placeholder-restore fallback.
"""

from unittest.mock import MagicMock, patch

from models import DoResult
from tools.doc_chips import (
    CHIP_REF_RE,
    parse_chip_refs,
    insert_chips_in_doc,
)


DOC_URL = "https://docs.google.com/document/d/1AbCdEfGh/edit"
SHEET_URL = "https://docs.google.com/spreadsheets/d/1XyZ/edit?gid=0"


class TestParseChipRefs:
    """The whole-line @url grain — mirroring Sheets' whole-cell opt-in."""

    def test_whole_line_workspace_url_becomes_a_placeholder(self):
        content = f"Intro line\n@{DOC_URL}\nOutro line"
        modified, refs = parse_chip_refs(content)
        assert len(refs) == 1
        assert refs[0].url == DOC_URL
        assert refs[0].placeholder in modified
        assert f"@{DOC_URL}" not in modified

    def test_mid_prose_url_is_not_a_chip(self):
        content = f"See @{DOC_URL} for details"
        modified, refs = parse_chip_refs(content)
        assert refs == []
        assert modified == content

    def test_non_workspace_url_keeps_its_literal_text(self):
        """insertRichLink rejects non-Drive URLs outright, and the batch is
        atomic — the gate here keeps one bad URL from failing every chip."""
        content = "@https://www.itv.com/\n"
        modified, refs = parse_chip_refs(content)
        assert refs == []
        assert "@https://www.itv.com/" in modified

    def test_multiple_chips_get_distinct_placeholders(self):
        content = f"@{DOC_URL}\nmiddle\n@{SHEET_URL}\n"
        modified, refs = parse_chip_refs(content)
        assert len(refs) == 2
        assert refs[0].placeholder != refs[1].placeholder
        assert refs[1].url == SHEET_URL

    def test_trailing_whitespace_tolerated(self):
        _, refs = parse_chip_refs(f"@{DOC_URL}   \n")
        assert len(refs) == 1

    def test_plain_at_mentions_are_not_matched(self):
        for line in ("@sameer.modha@itv.com", "@channel note", "email me @ home"):
            assert not CHIP_REF_RE.search(line)


class TestInsertChipsInDoc:
    """The atomic batchUpdate pass and its restore fallback."""

    def _doc_with(self, placeholders_at: dict[str, int]):
        """Doc body where each placeholder sits at its given start index."""
        content = []
        for ph, start in placeholders_at.items():
            content.append({
                "paragraph": {
                    "elements": [{
                        "startIndex": start,
                        "endIndex": start + len(ph),
                        "textRun": {"content": ph},
                    }]
                }
            })
        return {"body": {"content": content}}

    @patch("tools.doc_chips.get_sync_client")
    def test_inserts_uri_only_never_a_title(self, mock_get_client):
        """The probe-bought contract: a supplied title is a 400, and the live
        title is server-enriched — so the request must carry uri and nothing
        else."""
        _, refs = parse_chip_refs(f"@{DOC_URL}\n")
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_json.return_value = self._doc_with({refs[0].placeholder: 5})

        result = insert_chips_in_doc("doc1", refs)

        assert result == {"chips_inserted": 1}
        batch = client.post_json.call_args.kwargs["json_body"]["requests"]
        inserts = [r["insertRichLink"] for r in batch if "insertRichLink" in r]
        assert inserts == [{
            "location": {"index": 5, "segmentId": ""},
            "richLinkProperties": {"uri": DOC_URL},
        }]

    @patch("tools.doc_chips.get_sync_client")
    def test_sites_processed_in_reverse_index_order(self, mock_get_client):
        """Delete+insert mutate indices — later sites must go first."""
        _, refs = parse_chip_refs(f"@{DOC_URL}\n@{SHEET_URL}\n")
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_json.return_value = self._doc_with({
            refs[0].placeholder: 5,
            refs[1].placeholder: 50,
        })

        insert_chips_in_doc("doc1", refs)

        batch = client.post_json.call_args.kwargs["json_body"]["requests"]
        delete_starts = [
            r["deleteContentRange"]["range"]["startIndex"]
            for r in batch if "deleteContentRange" in r
        ]
        assert delete_starts == sorted(delete_starts, reverse=True)

    @patch("tools.doc_chips.get_sync_client")
    def test_batch_failure_restores_literal_text_and_reports(self, mock_get_client):
        """Atomicity means one bad chip fails all — the fallback puts @url text
        back so no sentinel residue survives in the document."""
        _, refs = parse_chip_refs(f"@{DOC_URL}\n")
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_json.return_value = self._doc_with({refs[0].placeholder: 5})
        client.post_json.side_effect = [Exception("boom"), {}]

        result = insert_chips_in_doc("doc1", refs)

        assert "chips_inserted" not in result
        assert any("Chip insertion failed" in e for e in result["chip_errors"])
        restore = client.post_json.call_args_list[1].kwargs["json_body"]["requests"]
        assert restore == [{
            "replaceAllText": {
                "containsText": {"text": refs[0].placeholder, "matchCase": True},
                "replaceText": f"@{DOC_URL}",
            }
        }]

    @patch("tools.doc_chips.get_sync_client")
    def test_missing_placeholder_is_an_error_not_a_crash(self, mock_get_client):
        _, refs = parse_chip_refs(f"@{DOC_URL}\n")
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_json.return_value = {"body": {"content": []}}

        result = insert_chips_in_doc("doc1", refs)

        assert any("not found" in e for e in result["chip_errors"])
        client.post_json.assert_not_called()

    def test_empty_refs_is_a_no_op(self):
        assert insert_chips_in_doc("doc1", []) == {}


class TestCreateWiring:
    """do_create parses chips out before import and inserts them after."""

    @patch("tools.create._insert_chips_in_doc", return_value={"chips_inserted": 1})
    @patch("tools.create._create_doc")
    def test_doc_create_routes_chips_and_cues_the_count(
        self, mock_create_doc, mock_insert
    ):
        from tools.create import do_create

        mock_create_doc.return_value = DoResult(
            file_id="new1", title="T", operation="create",
            web_link="https://docs.google.com/document/d/new1/edit",
        )

        result = do_create(
            doc_type="doc", title="T",
            content=f"Line one\n@{DOC_URL}\n",
        )

        uploaded = mock_create_doc.call_args[0][0]
        assert f"@{DOC_URL}" not in uploaded, "chip line must be a placeholder at import time"
        assert "MISE_CHIP_0" in uploaded
        (_, refs_arg) = mock_insert.call_args[0]
        assert refs_arg[0].url == DOC_URL
        assert result.cues["chips_inserted"] == 1


class TestOverwriteWiring:
    """do_overwrite on a Google Doc gets the same pass."""

    @patch("tools.overwrite.capture_restore_point", return_value={})
    @patch("tools.overwrite.insert_chips_in_doc", return_value={"chips_inserted": 1})
    @patch("tools.overwrite._overwrite_doc")
    def test_doc_overwrite_routes_chips_and_cues_the_count(
        self, mock_overwrite_doc, mock_insert, _mock_restore
    ):
        from tools.overwrite import do_overwrite

        mock_overwrite_doc.return_value = DoResult(
            file_id="f1", title="T", operation="overwrite",
            web_link="https://docs.google.com/document/d/f1/edit",
        )

        result = do_overwrite(
            file_id="f1",
            content=f"replacement\n@{DOC_URL}\n",
            metadata={"mimeType": "application/vnd.google-apps.document", "name": "T"},
        )

        uploaded = mock_overwrite_doc.call_args[0][1]
        assert f"@{DOC_URL}" not in uploaded
        assert "MISE_CHIP_0" in uploaded
        assert mock_insert.call_args[0][0] == "f1"
        assert result.cues["chips_inserted"] == 1
