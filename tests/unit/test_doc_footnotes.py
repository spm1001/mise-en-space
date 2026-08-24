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


class TestEssayeurCatches:
    """Regression pins for the 2026-08-24 essayeur findings (mise-rubucu)."""

    def test_fenced_code_is_not_footnote_syntax(self):
        content = (
            "Real[^1] anchor.\n\n"
            "```md\nuse [^1] to mark a footnote\n[^2]: this is not a definition\n```\n\n"
            "[^1]: Real definition.\n"
        )
        body, defs, warnings = parse_footnotes(content)

        assert defs == {"1": "Real definition."}
        assert "use [^1] to mark a footnote" in body  # fence content untouched
        assert "[^2]: this is not a definition" in body
        assert not any("[^2]" in w for w in warnings)  # code never warns

    def test_inline_code_is_not_an_anchor(self):
        content = "The syntax `[^1]` marks a footnote[^1].\n\n[^1]: Def.\n"
        body, defs, warnings = parse_footnotes(content)

        # Only the real anchor counts — one anchor + one def extracts cleanly.
        assert defs == {"1": "Def."}
        assert "`[^1]`" in body

    def test_duplicate_definition_left_literal_with_warning(self):
        content = "Use[^1] it.\n\n[^1]: First definition\n[^1]: Second definition\n"
        body, defs, warnings = parse_footnotes(content)

        assert defs == {}
        assert "[^1]: First definition" in body
        assert "[^1]: Second definition" in body
        assert any("defined 2 times" in w for w in warnings)

    def test_orphan_anchors_with_no_defs_at_all_still_warn(self):
        from tools.doc_footnotes import footnotes_for_import

        content, (defs, warnings) = footnotes_for_import("doc", "Bare anchor[^3] here.\n")

        assert defs == {}
        assert any("[^3]" in w and "no definitions" in w for w in warnings)

    def test_gfm_no_space_definition_parses(self):
        content = "Use[^1].\n\n[^1]:Tight definition\n"
        body, defs, warnings = parse_footnotes(content)

        assert defs == {"1": "Tight definition"}

    def test_crlf_definition_has_no_trailing_cr(self):
        content = "Use[^1].\r\n\r\n[^1]: Definition text\r\n"
        body, defs, warnings = parse_footnotes(content)

        assert defs == {"1": "Definition text"}

    @patch("tools.doc_footnotes.get_sync_client")
    @patch("tools.doc_chips.get_sync_client")
    def test_ambiguous_anchor_refused_not_guessed(
        self, mock_chips_client: MagicMock, mock_client: MagicMock
    ):
        client = MagicMock()
        mock_client.return_value = client
        mock_chips_client.return_value = client
        client.get_json.return_value = {
            "revisionId": "rev-1",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {"startIndex": 1, "endIndex": 30, "textRun": {"content": "One[^1] and again [^1] twice.\n"}}
                            ]
                        }
                    }
                ]
            },
        }
        client.post_json.return_value = {}

        result = insert_footnotes_in_doc("doc1", {"1": "Def."})

        assert "footnotes_inserted" not in result
        assert any("ambiguous" in e for e in result["footnote_errors"])
        # Only call: the definition restore — never a createFootnote guess.
        for call in client.post_json.call_args_list:
            reqs = call.kwargs["json_body"]["requests"]
            assert all("createFootnote" not in r for r in reqs)

    @patch("tools.doc_footnotes.get_sync_client")
    @patch("tools.doc_chips.get_sync_client")
    def test_write_control_pins_revision(
        self, mock_chips_client: MagicMock, mock_client: MagicMock
    ):
        client = MagicMock()
        mock_client.return_value = client
        mock_chips_client.return_value = client
        client.get_json.return_value = {
            "revisionId": "rev-42",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {"startIndex": 1, "endIndex": 17, "textRun": {"content": "Alpha[^1] beta.\n"}}
                            ]
                        }
                    }
                ]
            },
        }
        client.post_json.side_effect = [
            {"replies": [{"createFootnote": {"footnoteId": "kix.f1"}}, {}]},
            {},
        ]

        result = insert_footnotes_in_doc("doc1", {"1": "Def."})

        assert result == {"footnotes_inserted": 1}
        batch1 = client.post_json.call_args_list[0].kwargs["json_body"]
        assert batch1["writeControl"] == {"requiredRevisionId": "rev-42"}

    @patch("tools.doc_footnotes.get_sync_client")
    @patch("tools.doc_chips.get_sync_client")
    def test_fill_failure_reports_empty_footnotes_truthfully(
        self, mock_chips_client: MagicMock, mock_client: MagicMock
    ):
        client = MagicMock()
        mock_client.return_value = client
        mock_chips_client.return_value = client
        client.get_json.return_value = {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {"startIndex": 1, "endIndex": 17, "textRun": {"content": "Alpha[^1] beta.\n"}}
                            ]
                        }
                    }
                ]
            },
        }
        client.post_json.side_effect = [
            {"replies": [{"createFootnote": {"footnoteId": "kix.f1"}}, {}]},
            Exception("fill boom"),
            {},  # restore
        ]

        result = insert_footnotes_in_doc("doc1", {"1": "Def."})

        assert "footnotes_inserted" not in result
        errors = result["footnote_errors"]
        assert any("exist EMPTY" in e for e in errors)
        assert not any("anchors remain literal" in e for e in errors)  # the old false message


class TestUtf16Locator:
    """find_placeholder_meta counts in UTF-16 units (essayeur B1)."""

    @patch("tools.doc_chips.get_sync_client")
    def test_astral_chars_before_anchor(self, mock_client: MagicMock):
        from tools.doc_chips import find_placeholder_meta

        client = MagicMock()
        mock_client.return_value = client
        # "Hi 🚀🎉 x[^1] end.\n" — python len counts 🚀/🎉 as 1 each; Docs
        # counts 2 each. Paragraph starts at index 1.
        text = "Hi \U0001F680\U0001F389 x[^1] end.\n"
        client.get_json.return_value = {
            "revisionId": "r",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {"startIndex": 1, "textRun": {"content": text}}
                            ]
                        }
                    }
                ]
            },
        }

        found, counts, rev = find_placeholder_meta("doc1", ["[^1]"])

        # Before the anchor: "Hi 🚀🎉 x" = 7 code points but 9 UTF-16 units
        # (each emoji is a surrogate pair) — naive len() arithmetic would
        # report 8 and corrupt the doc (essayeur B1).
        assert found["[^1]"] == (1 + 9, 1 + 9 + 4)
        assert counts["[^1]"] == 1
        assert rev == "r"
