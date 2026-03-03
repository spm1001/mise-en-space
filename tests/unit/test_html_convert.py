"""Tests for html_convert.py — shared HTML cleaning and conversion utilities."""

from unittest.mock import patch

from html_convert import clean_html_for_conversion, convert_html_to_markdown, strip_html_tags


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
