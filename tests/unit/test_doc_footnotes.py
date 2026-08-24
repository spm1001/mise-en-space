"""
Markdown footnotes → real Docs footnotes (mise-rubucu).

The API contract these tests pin was bought with a live probe (2026-08-24,
scratch doc): one batchUpdate takes createFootnote-at-anchor-end +
deleteContentRange pairs in DESCENDING order; replies carry footnoteIds in
request order; a second batchUpdate inserts each definition at index 1 of
its footnote segment. Definitions are stripped pre-import, so every
failure path must put them back as literal text — content is never lost.
"""

from unittest.mock import MagicMock, patch

from tools.doc_footnotes import (
    FOOTNOTE_DEF_RE,
    insert_footnotes_in_doc,
    parse_footnotes,
)


class TestParseFootnotes:
    def test_matched_pair_extracted_and_def_stripped(self):
        content = "Alpha[^1] beta.\n\n[^1]: The definition.\n"
        body, defs, warnings = parse_footnotes(content)

        assert defs == {"1": "The definition."}
        assert "[^1]: The definition." not in body
        assert "Alpha[^1] beta." in body  # anchor stays for the post-import pass
        assert warnings == []

    def test_multiple_footnotes(self):
        content = (
            "One[^1] and two[^2].\n\n"
            "[^1]: First.\n"
            "[^2]: Second.\n"
        )
        body, defs, warnings = parse_footnotes(content)

        assert defs == {"1": "First.", "2": "Second."}
        assert "[^1]:" not in body and "[^2]:" not in body

    def test_def_without_anchor_left_in_place(self):
        content = "No anchors here.\n\n[^9]: Orphan definition.\n"
        body, defs, warnings = parse_footnotes(content)

        assert defs == {}
        assert "[^9]: Orphan definition." in body
        assert any("no matching anchor" in w for w in warnings)

    def test_anchor_without_def_warned(self):
        content = "Dangling[^7] anchor.\n\n[^1]: Real.\nUse[^1] it.\n"
        body, defs, warnings = parse_footnotes(content)

        assert defs == {"1": "Real."}
        assert any("[^7]" in w and "no definition" in w for w in warnings)

    def test_duplicate_anchor_left_literal(self):
        content = "Twice[^1] used[^1].\n\n[^1]: Def.\n"
        body, defs, warnings = parse_footnotes(content)

        assert defs == {}
        assert "[^1]: Def." in body
        assert any("appears 2 times" in w for w in warnings)

    def test_no_footnotes_is_identity(self):
        content = "Plain prose. A [link](https://example.com) too.\n"
        body, defs, warnings = parse_footnotes(content)

        assert body == content
        assert defs == {}
        assert warnings == []

    def test_regex_ignores_mid_line_bracket_text(self):
        content = "Array[^index] notation prose.\n"
        assert FOOTNOTE_DEF_RE.search(content) is None


class TestInsertFootnotes:
    def _doc_with_anchors(self, paragraphs: list[tuple[int, str]]) -> dict:
        return {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "startIndex": start,
                                    "endIndex": start + len(text),
                                    "textRun": {"content": text},
                                }
                            ]
                        }
                    }
                    for start, text in paragraphs
                ]
            }
        }

    @patch("tools.doc_footnotes.get_sync_client")
    @patch("tools.doc_chips.get_sync_client")
    def test_batch_built_descending_and_replies_mapped(
        self, mock_chips_client: MagicMock, mock_client: MagicMock
    ):
        client = MagicMock()
        mock_client.return_value = client
        mock_chips_client.return_value = client
        client.get_json.return_value = self._doc_with_anchors(
            [(1, "Alpha[^1] beta.\n"), (20, "Gamma[^2] delta.\n")]
        )
        client.post_json.side_effect = [
            {
                "replies": [
                    {"createFootnote": {"footnoteId": "kix.fn2"}},
                    {},
                    {"createFootnote": {"footnoteId": "kix.fn1"}},
                    {},
                ]
            },
            {},
        ]

        result = insert_footnotes_in_doc("doc1", {"1": "First.", "2": "Second."})

        assert result == {"footnotes_inserted": 2}
        first_batch = client.post_json.call_args_list[0].kwargs["json_body"]["requests"]
        # Descending: [^2] (start 25) processed before [^1] (start 6)
        assert first_batch[0]["createFootnote"]["location"]["index"] == 29
        assert first_batch[1]["deleteContentRange"]["range"] == {
            "startIndex": 25,
            "endIndex": 29,
        }
        assert first_batch[2]["createFootnote"]["location"]["index"] == 10
        second_batch = client.post_json.call_args_list[1].kwargs["json_body"]["requests"]
        # Reply order maps to request order: fn2 gets "Second.", fn1 "First."
        assert second_batch[0]["insertText"] == {
            "location": {"segmentId": "kix.fn2", "index": 1},
            "text": "Second.",
        }
        assert second_batch[1]["insertText"]["text"] == "First."

    @patch("tools.doc_footnotes.get_sync_client")
    @patch("tools.doc_chips.get_sync_client")
    def test_missing_anchor_definition_restored(
        self, mock_chips_client: MagicMock, mock_client: MagicMock
    ):
        client = MagicMock()
        mock_client.return_value = client
        mock_chips_client.return_value = client
        client.get_json.return_value = self._doc_with_anchors([(1, "No anchors here.\n")])
        client.post_json.return_value = {}

        result = insert_footnotes_in_doc("doc1", {"1": "Lost and found."})

        assert "footnotes_inserted" not in result
        errors = result["footnote_errors"]
        assert any("not found after import" in e for e in errors)
        restore = client.post_json.call_args_list[-1].kwargs["json_body"]["requests"][0]
        assert "[^1]: Lost and found." in restore["insertText"]["text"]
        assert restore["insertText"]["location" if "location" in restore["insertText"] else "endOfSegmentLocation"] == {}

    @patch("tools.doc_footnotes.get_sync_client")
    @patch("tools.doc_chips.get_sync_client")
    def test_pass_failure_restores_all_definitions(
        self, mock_chips_client: MagicMock, mock_client: MagicMock
    ):
        client = MagicMock()
        mock_client.return_value = client
        mock_chips_client.return_value = client
        client.get_json.return_value = self._doc_with_anchors([(1, "Alpha[^1] beta.\n")])
        client.post_json.side_effect = [Exception("boom"), {}]

        result = insert_footnotes_in_doc("doc1", {"1": "Definition."})

        errors = result["footnote_errors"]
        assert any("footnote pass failed" in e for e in errors)
        restore = client.post_json.call_args_list[-1].kwargs["json_body"]["requests"][0]
        assert "[^1]: Definition." in restore["insertText"]["text"]
