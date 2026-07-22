"""Unit tests for docs extractor."""

import pytest
from inline_snapshot import snapshot

from extractors.docs import (
    extract_doc_content,
    count_suggestions,
    annotate_suggestion_markup,
    _escape_markdown_link_text,
    _escape_markdown_url,
    _format_markdown_link,
    _to_alpha,
    _to_roman,
    _get_list_prefix,
)
from models import DocData, DocTab


# Fixture 'docs_response' is provided by tests/conftest.py


class TestExtractDocContent:
    """Tests for the main extraction function."""

    def test_basic_extraction(self, docs_response: DocData) -> None:
        """Test full extraction output with snapshot."""
        result = extract_doc_content(docs_response)
        assert result == snapshot("""\
# Project Proposal
This is a **bold** and *italic* text.
## Background
See the [documentation](https://example.com/docs) for more details[^1].
## Requirements
- First requirement
- Second requirement
  - Sub-item


---
[^1]: Additional context about the documentation.


============================================================
# Budget Breakdown
| Item | Cost |
|---|---|
| Development | $50,000 |
| Testing | $10,000 |

## Timeline
1. Phase 1: Planning
2. Phase 2: Development
3. Phase 3: Launch\
""")

    def test_truncation(self) -> None:
        """Test that content is truncated at max_length."""
        # Create a doc with lots of content
        long_text = "This is a very long paragraph. " * 100
        data = DocData(
            title="Long Doc",
            document_id="long-id",
            tabs=[
                DocTab(
                    title="Main",
                    tab_id="t.0",
                    index=0,
                    body={
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [
                                        {"textRun": {"content": long_text, "textStyle": {}}}
                                    ],
                                }
                            }
                        ]
                    },
                )
            ],
        )

        result = extract_doc_content(data, max_length=500)

        assert "TRUNCATED" in result

    def test_empty_doc(self) -> None:
        """Test handling of document with no tabs."""
        data = DocData(
            title="Empty",
            document_id="empty-id",
            tabs=[],
        )

        result = extract_doc_content(data)

        assert result == ""

    def test_single_tab_no_separator(self) -> None:
        """Test that single-tab doc has no separator."""
        data = DocData(
            title="Single",
            document_id="single-id",
            tabs=[
                DocTab(
                    title="Only Tab",
                    tab_id="t.0",
                    index=0,
                    body={
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [
                                        {"textRun": {"content": "Content here\n", "textStyle": {}}}
                                    ],
                                }
                            }
                        ]
                    },
                )
            ],
        )

        result = extract_doc_content(data)

        # No separator for single tab
        assert "=" * 60 not in result
        assert "Content here" in result


class TestHeadingWithIndentation:
    """Headings with indentation should NOT get blockquote prefixes."""

    def test_indented_heading_no_blockquote(self) -> None:
        """Indented heading produces '# text', not '> > # text'."""
        data = DocData(
            title="Indented Headings",
            document_id="ind-1",
            tabs=[
                DocTab(
                    title="Main",
                    tab_id="t.0",
                    index=0,
                    body={
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {
                                        "namedStyleType": "HEADING_1",
                                        "indentStart": {"magnitude": 72, "unit": "PT"},
                                    },
                                    "elements": [
                                        {"textRun": {"content": "1. First Section\n", "textStyle": {}}}
                                    ],
                                }
                            },
                            {
                                "paragraph": {
                                    "paragraphStyle": {
                                        "namedStyleType": "HEADING_1",
                                        "indentStart": {"magnitude": 72, "unit": "PT"},
                                    },
                                    "elements": [
                                        {"textRun": {"content": "2. Second Section\n", "textStyle": {}}}
                                    ],
                                }
                            },
                        ]
                    },
                )
            ],
        )
        result = extract_doc_content(data)
        assert "# 1. First Section" in result
        assert "# 2. Second Section" in result
        assert "> " not in result


class TestMarkdownEscaping:
    """Tests for markdown escaping helpers."""

    def test_escape_link_text_brackets(self) -> None:
        """Test that brackets are escaped in link text."""
        assert _escape_markdown_link_text("foo[bar]baz") == "foo\\[bar\\]baz"

    def test_escape_link_text_backslash(self) -> None:
        """Test that backslashes are escaped in link text."""
        assert _escape_markdown_link_text("foo\\bar") == "foo\\\\bar"

    def test_escape_url_parens(self) -> None:
        """Test that parentheses are percent-encoded in URLs."""
        assert _escape_markdown_url("https://example.com/foo(bar)") == "https://example.com/foo%28bar%29"

    def test_format_markdown_link(self) -> None:
        """Test full link formatting."""
        result = _format_markdown_link("Click [here]", "https://example.com/(test)")
        assert result == "[Click \\[here\\]](https://example.com/%28test%29)"


class TestInlineElements:
    """Tests for inline element handling (images, links, etc.)."""

    def test_inline_image(self) -> None:
        """Test that inline images are extracted with alt text."""
        data = DocData(
            title="Doc with Image",
            document_id="img-doc",
            tabs=[
                DocTab(
                    title="Main",
                    tab_id="t.0",
                    index=0,
                    body={
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [
                                        {"textRun": {"content": "See this: ", "textStyle": {}}},
                                        {"inlineObjectElement": {"inlineObjectId": "kix.abc123"}},
                                        {"textRun": {"content": "\n", "textStyle": {}}},
                                    ],
                                }
                            }
                        ]
                    },
                    inline_objects={
                        "kix.abc123": {
                            "inlineObjectProperties": {
                                "embeddedObject": {
                                    "title": "Architecture Diagram",
                                    "imageProperties": {
                                        "contentUri": "https://example.com/image.png"
                                    },
                                }
                            }
                        }
                    },
                )
            ],
        )

        result = extract_doc_content(data)
        assert "![Architecture Diagram](https://example.com/image.png)" in result

    def test_inline_drawing(self) -> None:
        """Test that drawings get a placeholder."""
        data = DocData(
            title="Doc with Drawing",
            document_id="draw-doc",
            tabs=[
                DocTab(
                    title="Main",
                    tab_id="t.0",
                    index=0,
                    body={
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [
                                        {"inlineObjectElement": {"inlineObjectId": "kix.draw1"}},
                                    ],
                                }
                            }
                        ]
                    },
                    inline_objects={
                        "kix.draw1": {
                            "inlineObjectProperties": {
                                "embeddedObject": {
                                    "title": "Flow Chart",
                                    "embeddedDrawingProperties": {},
                                }
                            }
                        }
                    },
                )
            ],
        )

        result = extract_doc_content(data)
        assert "[Drawing: Flow Chart]" in result

    def test_horizontal_rule(self) -> None:
        """Test that horizontal rules become markdown HR."""
        data = DocData(
            title="Doc with HR",
            document_id="hr-doc",
            tabs=[
                DocTab(
                    title="Main",
                    tab_id="t.0",
                    index=0,
                    body={
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [
                                        {"textRun": {"content": "Above\n", "textStyle": {}}},
                                    ],
                                }
                            },
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [
                                        {"horizontalRule": {}},
                                    ],
                                }
                            },
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [
                                        {"textRun": {"content": "Below\n", "textStyle": {}}},
                                    ],
                                }
                            },
                        ]
                    },
                )
            ],
        )

        result = extract_doc_content(data)
        assert "---" in result

    def test_rich_link(self) -> None:
        """Test that rich links (smart chips) become markdown links."""
        data = DocData(
            title="Doc with Rich Link",
            document_id="rich-doc",
            tabs=[
                DocTab(
                    title="Main",
                    tab_id="t.0",
                    index=0,
                    body={
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [
                                        {"textRun": {"content": "See event: ", "textStyle": {}}},
                                        {
                                            "richLink": {
                                                "richLinkProperties": {
                                                    "title": "Team Meeting",
                                                    "uri": "https://calendar.google.com/event/abc",
                                                }
                                            }
                                        },
                                        {"textRun": {"content": "\n", "textStyle": {}}},
                                    ],
                                }
                            }
                        ]
                    },
                )
            ],
        )

        result = extract_doc_content(data)
        assert "[Team Meeting](https://calendar.google.com/event/abc)" in result

    def test_person_mention(self) -> None:
        """Test that @mentions are preserved."""
        data = DocData(
            title="Doc with Mention",
            document_id="mention-doc",
            tabs=[
                DocTab(
                    title="Main",
                    tab_id="t.0",
                    index=0,
                    body={
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [
                                        {"textRun": {"content": "Ask ", "textStyle": {}}},
                                        {
                                            "person": {
                                                "personId": "12345",
                                                "personProperties": {
                                                    "name": "Alice Smith",
                                                    "email": "alice@example.com",
                                                },
                                            }
                                        },
                                        {"textRun": {"content": " about this.\n", "textStyle": {}}},
                                    ],
                                }
                            }
                        ]
                    },
                )
            ],
        )

        result = extract_doc_content(data)
        assert "@Alice Smith" in result

    def test_unknown_inline_object(self) -> None:
        """Test that unknown inline objects show object ID."""
        data = DocData(
            title="Doc with Unknown",
            document_id="unknown-doc",
            tabs=[
                DocTab(
                    title="Main",
                    tab_id="t.0",
                    index=0,
                    body={
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [
                                        {"inlineObjectElement": {"inlineObjectId": "kix.unknown"}},
                                    ],
                                }
                            }
                        ]
                    },
                    inline_objects={},  # Object not in dict
                )
            ],
        )

        result = extract_doc_content(data)
        assert "[object:kix.unknown]" in result

    def test_linked_chart(self) -> None:
        """Test that linked Sheets charts are identified."""
        data = DocData(
            title="Doc with Chart",
            document_id="chart-doc",
            tabs=[
                DocTab(
                    title="Main",
                    tab_id="t.0",
                    index=0,
                    body={
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [
                                        {"inlineObjectElement": {"inlineObjectId": "kix.chart1"}},
                                    ],
                                }
                            }
                        ]
                    },
                    inline_objects={
                        "kix.chart1": {
                            "inlineObjectProperties": {
                                "embeddedObject": {
                                    "title": "Q4 Revenue",
                                    "linkedContentReference": {
                                        "sheetsChartReference": {
                                            "spreadsheetId": "abc123",
                                            "chartId": 456,
                                        }
                                    },
                                }
                            }
                        }
                    },
                )
            ],
        )

        result = extract_doc_content(data)
        assert "[Chart: Q4 Revenue (from spreadsheet abc123)]" in result


class TestListHelpers:
    """Tests for list formatting helpers."""

    def test_to_alpha_lowercase(self) -> None:
        """Test alphabetic conversion lowercase."""
        assert _to_alpha(1) == "a"
        assert _to_alpha(26) == "z"
        assert _to_alpha(27) == "aa"
        assert _to_alpha(28) == "ab"

    def test_to_alpha_uppercase(self) -> None:
        """Test alphabetic conversion uppercase."""
        assert _to_alpha(1, lowercase=False) == "A"
        assert _to_alpha(26, lowercase=False) == "Z"

    def test_to_roman_lowercase(self) -> None:
        """Test roman numeral conversion lowercase."""
        assert _to_roman(1) == "i"
        assert _to_roman(4) == "iv"
        assert _to_roman(5) == "v"
        assert _to_roman(9) == "ix"
        assert _to_roman(10) == "x"
        assert _to_roman(14) == "xiv"

    def test_to_roman_uppercase(self) -> None:
        """Test roman numeral conversion uppercase."""
        assert _to_roman(1, lowercase=False) == "I"
        assert _to_roman(4, lowercase=False) == "IV"

    def test_get_list_prefix_bullet(self) -> None:
        """Test bullet list prefix."""
        lists = {"list1": {"listProperties": {"nestingLevels": [{"glyphType": "BULLET"}]}}}
        counters: dict = {}

        result = _get_list_prefix(lists, "list1", 0, counters)
        assert result == "- "

    def test_get_list_prefix_decimal(self) -> None:
        """Test numbered list prefix."""
        lists = {"list1": {"listProperties": {"nestingLevels": [{"glyphType": "DECIMAL"}]}}}
        counters: dict = {}

        result1 = _get_list_prefix(lists, "list1", 0, counters)
        result2 = _get_list_prefix(lists, "list1", 0, counters)
        result3 = _get_list_prefix(lists, "list1", 0, counters)

        assert result1 == "1. "
        assert result2 == "2. "
        assert result3 == "3. "

    def test_get_list_prefix_nested(self) -> None:
        """Test nested list indentation."""
        lists = {
            "list1": {
                "listProperties": {
                    "nestingLevels": [
                        {"glyphType": "BULLET"},
                        {"glyphType": "BULLET"},
                    ]
                }
            }
        }
        counters: dict = {}

        result0 = _get_list_prefix(lists, "list1", 0, counters)
        result1 = _get_list_prefix(lists, "list1", 1, counters)

        assert result0 == "- "
        assert result1 == "  - "  # 2 spaces indent


# ============================================================================
# SUGGESTED EDITS (mise-wofomu)
# ============================================================================

def _sugg_doc() -> DocData:
    """One replace (insert+delete sharing an id) + one pure-delete line."""
    body = {
        "content": [
            {"paragraph": {"elements": [
                {"textRun": {"content": "Six feet under screams, "}},
                {"textRun": {
                    "content": "but no one cares about this song",
                    "suggestedInsertionIds": ["suggest.abc"],
                }},
                {"textRun": {
                    "content": "but no one seems to hear a thing\n",
                    "suggestedDeletionIds": ["suggest.abc"],
                }},
            ]}},
            {"paragraph": {"elements": [
                {"textRun": {
                    "content": "'Cause there's a spark in you\n",
                    "suggestedDeletionIds": ["suggest.def"],
                }},
            ]}},
        ]
    }
    return DocData(
        title="Firework",
        document_id="doc123",
        tabs=[DocTab(title="Tab 1", tab_id="t.0", index=0, body=body)],
    )


class TestSuggestions:
    """count_suggestions / annotate_suggestion_markup / markup rendering."""

    def test_count_distinct_ids(self) -> None:
        data = _sugg_doc()
        assert count_suggestions(data.tabs) == 2  # replace pair shares one id

    def test_count_zero_on_clean_doc(self, docs_response: DocData) -> None:
        assert count_suggestions(docs_response.tabs) == 0

    def test_count_recurses_into_tables(self) -> None:
        body = {"content": [{"table": {"tableRows": [{"tableCells": [{"content": [
            {"paragraph": {"elements": [
                {"textRun": {"content": "cell edit", "suggestedInsertionIds": ["suggest.t1"]}},
            ]}},
        ]}]}]}}]}
        tabs = [DocTab(title="T", tab_id="t", index=0, body=body)]
        assert count_suggestions(tabs) == 1

    def test_annotate_assigns_shared_tags(self) -> None:
        data = _sugg_doc()
        count = annotate_suggestion_markup(data.tabs)
        assert count == 2
        runs = [
            e["textRun"]
            for p in data.tabs[0].body["content"]
            for e in p["paragraph"]["elements"]
            if "textRun" in e and e["textRun"].get("_mise_suggestion_kind")
        ]
        assert [r["_mise_suggestion_kind"] for r in runs] == ["ins", "del", "del"]
        # replace pair shares s1; the standalone delete gets s2
        assert [r["_mise_suggestion_tag"] for r in runs] == ["s1", "s1", "s2"]

    def test_annotate_deletion_dominates_double_tagged_run(self) -> None:
        body = {"content": [{"paragraph": {"elements": [
            {"textRun": {
                "content": "ghost",
                "suggestedInsertionIds": ["suggest.a"],
                "suggestedDeletionIds": ["suggest.b"],
            }},
        ]}}]}
        tabs = [DocTab(title="T", tab_id="t", index=0, body=body)]
        annotate_suggestion_markup(tabs)
        run = tabs[0].body["content"][0]["paragraph"]["elements"][0]["textRun"]
        assert run["_mise_suggestion_kind"] == "del"

    def test_markup_rendering(self) -> None:
        data = _sugg_doc()
        annotate_suggestion_markup(data.tabs)
        result = extract_doc_content(data)
        assert result == snapshot("""\
# Tab 1

Six feet under screams, {++but no one cares about this song++}[s1]{--but no one seems to hear a thing--}[s1]
{--'Cause there's a spark in you--}[s2]\
""")

    def test_unannotated_suggestions_render_inline(self) -> None:
        """Without annotation (non-markup modes never see suggestion runs), text passes through untouched."""
        data = _sugg_doc()
        result = extract_doc_content(data)
        assert "{++" not in result and "{--" not in result
