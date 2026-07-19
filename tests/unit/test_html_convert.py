"""Tests for html_convert.py — shared HTML cleaning and conversion utilities."""

from unittest.mock import patch

from html_convert import (
    clean_html_for_conversion,
    convert_html_to_markdown,
    html_to_text_with_links,
    markdown_to_html,
    strip_html_tags,
)


class TestCleanHtmlForConversion:
    """Tests for the pre-conversion HTML cleaning function."""

    def test_removes_hidden_line_breaks(self) -> None:
        html = 'Hello<br style="display:none"/>World'
        result = clean_html_for_conversion(html)
        assert "display:none" not in result
        assert "Hello" in result
        assert "World" in result

    def test_removes_mso_conditionals(self) -> None:
        html = '<p>Keep</p><!--[if mso]><b>Outlook only</b><![endif]--><p>Also keep</p>'
        result = clean_html_for_conversion(html)
        assert "Outlook only" not in result
        assert "Keep" in result
        assert "Also keep" in result

    def test_removes_tracking_pixels(self) -> None:
        html = '<p>Content</p><img width="1" height="1" src="https://track.example.com/pixel.gif"/>'
        result = clean_html_for_conversion(html)
        assert "track.example.com" not in result
        assert "Content" in result

    def test_removes_simple_hidden_element(self) -> None:
        html = '<p>Visible</p><span style="display:none">Hidden</span><p>Also visible</p>'
        result = clean_html_for_conversion(html)
        assert "Hidden" not in result
        assert "Visible" in result
        assert "Also visible" in result

    def test_removes_nested_hidden_elements(self) -> None:
        """The critical test: nested tags inside display:none must be fully removed."""
        html = '<p>Before</p><div style="display:none"><div>Nested inner</div></div><p>After</p>'
        result = clean_html_for_conversion(html)
        assert "Nested inner" not in result
        assert "Before" in result
        assert "After" in result

    def test_removes_spacer_cells(self) -> None:
        html = '<table><tr><td>Real</td><td>&nbsp;</td></tr></table>'
        result = clean_html_for_conversion(html)
        assert "Real" in result

    def test_removes_empty_paragraphs(self) -> None:
        html = '<p>Content</p><p>&nbsp;</p><p>More</p>'
        result = clean_html_for_conversion(html)
        assert "Content" in result
        assert "More" in result

    def test_returns_empty_for_empty_input(self) -> None:
        assert clean_html_for_conversion("") == ""

    def test_returns_none_for_none_input(self) -> None:
        assert clean_html_for_conversion(None) is None

    def test_preserves_visible_content(self) -> None:
        html = '<p>Hello <b>world</b>!</p>'
        result = clean_html_for_conversion(html)
        assert "Hello" in result
        assert "world" in result


class TestConvertHtmlToMarkdown:
    """Tests for the markitdown-based HTML conversion."""

    def test_converts_simple_html(self) -> None:
        html = '<h1>Title</h1><p>Body text</p>'
        result, used_fallback = convert_html_to_markdown(html)
        assert "Title" in result
        assert "Body text" in result
        assert not used_fallback

    def test_empty_input_returns_empty(self) -> None:
        result, used_fallback = convert_html_to_markdown("")
        assert result == ""
        assert not used_fallback

    def test_whitespace_only_returns_empty(self) -> None:
        result, used_fallback = convert_html_to_markdown("   \n  ")
        assert result == ""
        assert not used_fallback

    def test_fallback_on_markitdown_failure(self) -> None:
        with patch("markitdown.MarkItDown", side_effect=Exception("broken")):
            result, used_fallback = convert_html_to_markdown("<p>Hello</p>")
            assert "Hello" in result
            assert used_fallback


class TestStripHtmlTags:
    """Tests for the pure tag-stripping fallback."""

    def test_strips_tags(self) -> None:
        assert strip_html_tags("<p>Hello <b>world</b></p>") == "Hello world"

    def test_collapses_whitespace(self) -> None:
        assert strip_html_tags("<p>  Hello  </p>  <p>  World  </p>") == "Hello World"

    def test_empty_input(self) -> None:
        assert strip_html_tags("") == ""


class TestMarkdownToHtml:
    """Tests for markdown→HTML (email draft bodies — field report mise-zolowa)."""

    def test_gfm_table_renders(self) -> None:
        result = markdown_to_html("| A | B |\n|---|---|\n| 1 | 2 |")
        assert "<table>" in result
        assert "<th>A</th>" in result
        assert "<td>1</td>" in result

    def test_bold_and_italic(self) -> None:
        result = markdown_to_html("**bold** and *italic*")
        assert "<strong>bold</strong>" in result
        assert "<em>italic</em>" in result

    def test_headings(self) -> None:
        assert "<h1>Title</h1>" in markdown_to_html("# Title")
        assert "<h2>Sub</h2>" in markdown_to_html("## Sub")

    def test_single_newline_becomes_br(self) -> None:
        # nl2br + output_format=html → <br> (not <br />)
        assert "<br>" in markdown_to_html("a\nb")

    def test_bare_ampersand_escaped(self) -> None:
        assert "&amp;" in markdown_to_html("AT&T")

    def test_plain_text_wrapped_in_paragraph(self) -> None:
        assert markdown_to_html("Hello world") == "<p>Hello world</p>"

    def test_empty_input(self) -> None:
        assert markdown_to_html("") == ""


class TestHtmlToTextWithLinks:
    """html_to_text_with_links — signature rendering for text/plain parts."""

    def test_link_becomes_text_with_url(self) -> None:
        html = '<a href="https://example.com/x">Visit us</a>'
        assert html_to_text_with_links(html) == "Visit us (https://example.com/x)"

    def test_bare_url_link_not_doubled(self) -> None:
        html = '<a href="https://example.com">https://example.com</a>'
        assert html_to_text_with_links(html) == "https://example.com"

    def test_mailto_wrapping_its_own_address(self) -> None:
        html = '<a href="mailto:a@b.com">a@b.com</a>'
        assert html_to_text_with_links(html) == "a@b.com"

    def test_block_tags_become_newlines(self) -> None:
        html = "<div>Line one</div><div>Line two</div>"
        assert html_to_text_with_links(html) == "Line one\nLine two"

    def test_br_variants(self) -> None:
        assert html_to_text_with_links("a<br>b") == "a\nb"
        assert html_to_text_with_links("a<br/>b") == "a\nb"

    def test_entities_unescaped(self) -> None:
        assert html_to_text_with_links("<p>Fish &amp; Chips</p>") == "Fish & Chips"

    def test_blank_runs_collapse(self) -> None:
        html = "<div>a</div><div><br></div><div><br></div><div>b</div>"
        assert html_to_text_with_links(html) == "a\n\nb"

    def test_empty_input(self) -> None:
        assert html_to_text_with_links("") == ""
        assert html_to_text_with_links("   ") == ""

    def test_realistic_signature(self) -> None:
        # Shape of a real Gmail sendAs signature: nested divs, styled links
        html = (
            '<div dir="ltr"><div>Sam</div><div><br></div>'
            '<div>Measurement Team</div>'
            '<div>Visit <a href="https://maps.example/q" style="color:blue" '
            'target="_blank">our office</a></div></div>'
        )
        text = html_to_text_with_links(html)
        assert "Sam" in text
        assert "our office (https://maps.example/q)" in text
        assert "style" not in text  # attributes never leak
