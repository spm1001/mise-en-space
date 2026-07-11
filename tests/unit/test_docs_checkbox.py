"""Unit tests for Google Docs checkbox (tick-box) support.

Checkbox checked-state is NOT exposed by the Docs API — a checked and an
unchecked row are byte-identical there. It's resolved via the Drive markdown
export oracle (adapters/docs.py annotates checkbox paragraphs in document
order), then rendered here as GFM task-list markers. See mise-newosi / mise-pirozu.
"""

from extractors.docs import (
    annotate_checkbox_states,
    extract_doc_content,
    is_checkbox_list,
    parse_checkbox_markers,
)
from models import DocData, DocTab

# A checkbox list uses glyphType GLYPH_TYPE_UNSPECIFIED with no glyphSymbol;
# an ordinary bullet list carries a glyphSymbol.
CHECKBOX_LIST = {"listProperties": {"nestingLevels": [{"glyphType": "GLYPH_TYPE_UNSPECIFIED"}]}}
BULLET_LIST = {"listProperties": {"nestingLevels": [{"glyphSymbol": "-"}]}}


def _para(text, list_id=None, level=0, checked=None):
    p = {"paragraphStyle": {}, "elements": [{"textRun": {"content": text, "textStyle": {}}}]}
    if list_id is not None:
        p["bullet"] = {"listId": list_id, "nestingLevel": level, "textStyle": {}}
    if checked is not None:
        p["_mise_checkbox_checked"] = checked
    return {"paragraph": p}


def _tab(content, lists=None):
    return DocTab(title="T", tab_id="t.0", index=0, body={"content": content}, lists=lists or {})


def _doc(content, lists=None, adapter_warnings=None):
    return DocData(
        title="D",
        document_id="id",
        tabs=[_tab(content, lists)],
        adapter_warnings=adapter_warnings or [],
    )


class TestParseCheckboxMarkers:
    def test_basic(self):
        md = "- [ ] alpha\n- [x] bravo\n- [X] charlie\n- plain\nnot a list"
        assert parse_checkbox_markers(md) == [False, True, True]

    def test_star_bullets_and_indent(self):
        md = "  * [x] indented star\n* [ ] flat"
        assert parse_checkbox_markers(md) == [True, False]

    def test_empty(self):
        assert parse_checkbox_markers("") == []

    def test_ignores_non_checkbox_lines(self):
        assert parse_checkbox_markers("# heading\n- bullet\ntext [x] mid-line") == []


class TestIsCheckboxList:
    def test_checkbox(self):
        assert is_checkbox_list(CHECKBOX_LIST) is True

    def test_bullet(self):
        assert is_checkbox_list(BULLET_LIST) is False

    def test_empty(self):
        assert is_checkbox_list({}) is False


class TestAnnotate:
    def test_count_match_tags_checkbox_paras(self):
        content = [_para("a", "L1"), _para("plain"), _para("b", "L1")]
        tab = _tab(content, {"L1": CHECKBOX_LIST})
        warning = annotate_checkbox_states([tab], [True, False])
        assert warning is None
        assert content[0]["paragraph"]["_mise_checkbox_checked"] is True
        assert content[2]["paragraph"]["_mise_checkbox_checked"] is False
        # the non-list paragraph is untouched
        assert "_mise_checkbox_checked" not in content[1]["paragraph"]

    def test_count_mismatch_warns_and_tags_nothing(self):
        content = [_para("a", "L1"), _para("b", "L1")]
        tab = _tab(content, {"L1": CHECKBOX_LIST})
        warning = annotate_checkbox_states([tab], [True])  # 2 items, 1 state
        assert warning is not None and "suppressed" in warning
        assert "_mise_checkbox_checked" not in content[0]["paragraph"]
        assert "_mise_checkbox_checked" not in content[1]["paragraph"]

    def test_bullet_list_is_not_a_checkbox(self):
        content = [_para("a", "L1")]
        tab = _tab(content, {"L1": BULLET_LIST})
        # zero checkbox items vs zero states -> no warning, nothing tagged
        assert annotate_checkbox_states([tab], []) is None
        assert "_mise_checkbox_checked" not in content[0]["paragraph"]

    def test_recurses_into_table_cells(self):
        cell = {"content": [_para("in cell", "L1")]}
        table = {"table": {"tableRows": [{"tableCells": [cell]}]}}
        content = [_para("top", "L1"), table]
        tab = _tab(content, {"L1": CHECKBOX_LIST})
        # 2 checkbox items in reading order: "top" then "in cell"
        assert annotate_checkbox_states([tab], [True, False]) is None
        assert content[0]["paragraph"]["_mise_checkbox_checked"] is True
        assert cell["content"][0]["paragraph"]["_mise_checkbox_checked"] is False


class TestRender:
    def test_checked(self):
        result = extract_doc_content(
            _doc([_para("Done thing\n", "L1", checked=True)], {"L1": CHECKBOX_LIST})
        )
        assert "- [x] Done thing" in result

    def test_unchecked(self):
        result = extract_doc_content(
            _doc([_para("Todo thing\n", "L1", checked=False)], {"L1": CHECKBOX_LIST})
        )
        assert "- [ ] Todo thing" in result

    def test_nested_indent(self):
        result = extract_doc_content(
            _doc([_para("Nested\n", "L1", level=1, checked=True)], {"L1": CHECKBOX_LIST})
        )
        assert "  - [x] Nested" in result

    def test_unannotated_checkbox_falls_back_to_plain_bullet(self):
        # A checkbox list item with NO annotation (export desync) must render a
        # plain bullet — never a guessed [ ]/[x].
        result = extract_doc_content(
            _doc([_para("Unknown\n", "L1")], {"L1": CHECKBOX_LIST})
        )
        assert "- Unknown" in result
        assert "[ ]" not in result and "[x]" not in result

    def test_adapter_warning_is_merged_into_warnings(self):
        doc = _doc(
            [_para("x\n", "L1")],
            {"L1": CHECKBOX_LIST},
            adapter_warnings=["Checkbox tick-state suppressed: mismatch."],
        )
        extract_doc_content(doc)
        assert any("suppressed" in w for w in doc.warnings)
