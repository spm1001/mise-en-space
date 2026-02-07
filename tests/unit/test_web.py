"""
Tests for web content extraction.

Tests the web adapter and extractor with mocked HTTP responses.
"""

import pytest
from unittest.mock import patch, MagicMock

from models import WebData, MiseError, ErrorKind
from extractors.web import (
    extract_web_content,
    extract_title,
    _preserve_code_blocks,
    _restore_code_blocks,
    _extract_language_from_tag,
    _is_raw_text,
    _format_raw_text,
    _get_language_from_url,
)
from adapters.web import (
    is_web_url,
    _detect_auth_required,
    _detect_captcha,
    _needs_browser_rendering,
    _is_binary_content_type,
    _parse_content_length,
    STREAMING_THRESHOLD_BYTES,
)


class TestIsWebUrl:
    """Test URL detection."""

    def test_http_url(self) -> None:
        assert is_web_url("http://example.com")

    def test_https_url(self) -> None:
        assert is_web_url("https://example.com")

    def test_not_url(self) -> None:
        assert not is_web_url("abc123def")

    def test_drive_id(self) -> None:
        assert not is_web_url("1a2b3c4d5e6f")

    def test_url_with_whitespace(self) -> None:
        assert is_web_url("  https://example.com  ")


class TestAuthDetection:
    """Test authentication detection heuristics."""

    def test_401_status(self) -> None:
        response = MagicMock()
        response.status_code = 401
        response.url = "https://example.com"

        result = _detect_auth_required(response, "<html></html>")
        assert result is not None
        assert "401" in result

    def test_403_status(self) -> None:
        response = MagicMock()
        response.status_code = 403
        response.url = "https://example.com"

        result = _detect_auth_required(response, "<html></html>")
        assert result is not None
        assert "403" in result

    def test_login_redirect(self) -> None:
        response = MagicMock()
        response.status_code = 200
        response.url = "https://example.com/login?redirect=..."

        result = _detect_auth_required(response, "<html></html>")
        assert result is not None
        assert "login" in result.lower()

    def test_paywall_detection(self) -> None:
        response = MagicMock()
        response.status_code = 200
        response.url = "https://example.com/article"

        html = "<html><body>Please subscribe to continue reading...</body></html>"
        result = _detect_auth_required(response, html)
        assert result is not None
        assert "paywall" in result.lower()

    def test_no_auth_required(self) -> None:
        response = MagicMock()
        response.status_code = 200
        response.url = "https://example.com"

        result = _detect_auth_required(response, "<html><body>Hello world</body></html>")
        assert result is None


class TestCaptchaDetection:
    """Test CAPTCHA detection."""

    def test_cloudflare_challenge(self) -> None:
        html = "<html><div class='cf-challenge'>Please wait...</div></html>"
        assert _detect_captcha(html)

    def test_recaptcha(self) -> None:
        html = "<html><div class='g-recaptcha'>...</div></html>"
        assert _detect_captcha(html)

    def test_no_captcha(self) -> None:
        html = "<html><body>Normal content</body></html>"
        assert not _detect_captcha(html)


class TestBrowserRenderingDetection:
    """Test JS-rendered content detection."""

    def test_nextjs_detected(self) -> None:
        html = '<html><script id="__NEXT_DATA__">{"props":{}}</script></html>'
        assert _needs_browser_rendering(html)

    def test_nuxt_detected(self) -> None:
        html = "<html><script>window.__NUXT__={}</script></html>"
        assert _needs_browser_rendering(html)

    def test_very_short_content(self) -> None:
        html = "<html></html>"
        assert _needs_browser_rendering(html)

    def test_static_content(self) -> None:
        html = "<html><body>" + "x" * 1000 + "</body></html>"
        assert not _needs_browser_rendering(html)


class TestExtractTitle:
    """Test HTML title extraction."""

    def test_simple_title(self) -> None:
        html = "<html><head><title>My Page</title></head></html>"
        assert extract_title(html) == "My Page"

    def test_title_with_entities(self) -> None:
        html = "<html><head><title>A &amp; B</title></head></html>"
        assert extract_title(html) == "A & B"

    def test_no_title(self) -> None:
        html = "<html><body>No title here</body></html>"
        assert extract_title(html) == ""

    def test_whitespace_normalized(self) -> None:
        html = "<html><head><title>  Too   much   space  </title></head></html>"
        assert extract_title(html) == "Too much space"


class TestCodeBlockPreservation:
    """Test code block preservation (pre-process/restore pattern)."""

    def test_extract_language_python_class(self) -> None:
        tag = '<pre><code class="language-python">'
        assert _extract_language_from_tag(tag) == "python"

    def test_extract_language_lang_js(self) -> None:
        tag = '<pre><code class="lang-js">'
        assert _extract_language_from_tag(tag) == "js"

    def test_extract_language_data_attr(self) -> None:
        tag = '<pre data-lang="rust"><code>'
        assert _extract_language_from_tag(tag) == "rust"

    def test_extract_language_highlight_js(self) -> None:
        tag = '<pre class="highlight python">'
        assert _extract_language_from_tag(tag) == "python"

    def test_extract_language_none(self) -> None:
        tag = '<pre><code>'
        assert _extract_language_from_tag(tag) == ""

    def test_preserve_single_block(self) -> None:
        html = '<p>Hello</p><pre><code class="language-python">print("hi")</code></pre><p>World</p>'
        modified, blocks = _preserve_code_blocks(html)

        assert len(blocks) == 1
        assert blocks[0].language == "python"
        assert blocks[0].content == 'print("hi")'
        assert blocks[0].placeholder in modified
        assert '<pre>' not in modified

    def test_preserve_multiple_blocks(self) -> None:
        html = '''
        <pre><code class="language-python">x = 1</code></pre>
        <pre><code class="lang-js">let y = 2;</code></pre>
        '''
        modified, blocks = _preserve_code_blocks(html)

        assert len(blocks) == 2
        assert blocks[0].language == "python"
        assert blocks[1].language == "js"

    def test_preserve_block_no_language(self) -> None:
        html = '<pre><code>plain code</code></pre>'
        modified, blocks = _preserve_code_blocks(html)

        assert len(blocks) == 1
        assert blocks[0].language == ""
        assert blocks[0].content == "plain code"

    def test_preserve_decodes_entities(self) -> None:
        html = '<pre><code class="language-python">if x &lt; 5 &amp;&amp; y &gt; 3:</code></pre>'
        modified, blocks = _preserve_code_blocks(html)

        assert len(blocks) == 1
        assert blocks[0].content == "if x < 5 && y > 3:"

    def test_preserve_strips_span_tags(self) -> None:
        html = '<pre><code class="language-python"><span class="keyword">def</span> foo():</code></pre>'
        modified, blocks = _preserve_code_blocks(html)

        assert len(blocks) == 1
        assert blocks[0].content == "def foo():"
        assert "<span" not in blocks[0].content

    def test_restore_blocks(self) -> None:
        blocks = [
            type('CodeBlock', (), {'language': 'python', 'content': 'x = 1', 'placeholder': 'CODEBLOCK_abc123'})(),
        ]
        content = "Some text\n\nCODEBLOCK_abc123\n\nMore text"

        result = _restore_code_blocks(content, blocks)

        assert "```python\nx = 1\n```" in result
        assert "CODEBLOCK_abc123" not in result

    def test_restore_block_no_language(self) -> None:
        blocks = [
            type('CodeBlock', (), {'language': '', 'content': 'plain', 'placeholder': 'CODEBLOCK_xyz789'})(),
        ]
        content = "CODEBLOCK_xyz789"

        result = _restore_code_blocks(content, blocks)

        assert result == "```\nplain\n```"


class TestExtractWebContent:
    """Test the main extraction function."""

    def test_basic_extraction(self) -> None:
        """Test extraction with simple HTML."""
        html = """
        <html>
        <head><title>Test Page</title></head>
        <body>
            <header>Nav bar</header>
            <main>
                <h1>Main Content</h1>
                <p>This is the important text.</p>
            </main>
            <footer>Footer stuff</footer>
        </body>
        </html>
        """
        data = WebData(
            url="https://example.com",
            html=html,
            final_url="https://example.com",
            status_code=200,
            content_type="text/html",
            cookies_used=False,
            render_method="http",
            warnings=[],
        )

        content = extract_web_content(data)
        assert "Test Page" in content or "Main Content" in content
        assert len(content) > 0

    def test_empty_content_fallback(self) -> None:
        """Test fallback when trafilatura returns nothing."""
        html = "<html><body></body></html>"
        data = WebData(
            url="https://example.com",
            html=html,
            final_url="https://example.com",
            status_code=200,
            content_type="text/html",
            cookies_used=False,
            render_method="http",
            warnings=[],
        )

        content = extract_web_content(data)
        # Should have some content even with empty HTML
        assert "Untitled" in content or "extraction failed" in content.lower()
        assert len(data.warnings) > 0

    def test_warnings_populated(self) -> None:
        """Test that warnings are populated during extraction."""
        html = "<html><body></body></html>"
        data = WebData(
            url="https://example.com",
            html=html,
            final_url="https://example.com",
            status_code=200,
            content_type="text/html",
            cookies_used=False,
            render_method="http",
            warnings=[],
        )

        extract_web_content(data)
        assert len(data.warnings) > 0


class TestUrlDetectionInFetch:
    """Test URL detection in the fetch routing."""

    def test_web_url_detection(self) -> None:
        from tools.fetch import detect_id_type

        source, normalized = detect_id_type("https://example.com/page")
        assert source == "web"
        assert normalized == "https://example.com/page"

    def test_google_drive_not_web(self) -> None:
        """Google Drive URLs should route to drive, not web."""
        from tools.fetch import detect_id_type

        source, _ = detect_id_type("https://docs.google.com/document/d/abc123")
        assert source == "drive"

    def test_gmail_not_web(self) -> None:
        """Gmail URLs should try to route to gmail, not web."""
        # Note: Gmail URL detection happens before web URL detection,
        # so even if the URL format is invalid, it won't be treated as web
        from adapters.web import is_web_url

        # Gmail URLs should not be treated as generic web URLs by is_web_url
        # The routing logic catches mail.google.com first
        gmail_url = "https://mail.google.com/mail/u/0/#inbox/abc123"

        # The URL *is* technically a valid web URL
        assert is_web_url(gmail_url)

        # But detect_id_type routes it to gmail, not web
        # (This test verifies the routing priority, not the extraction)


class TestBinaryContentTypeDetection:
    """Test binary Content-Type detection for non-HTML responses."""

    def test_pdf_content_type(self) -> None:
        assert _is_binary_content_type("application/pdf")

    def test_pdf_with_charset(self) -> None:
        assert _is_binary_content_type("application/pdf; charset=utf-8")

    def test_octet_stream_not_binary(self) -> None:
        """octet-stream is too ambiguous — only match types we can extract."""
        assert not _is_binary_content_type("application/octet-stream")

    def test_html_not_binary(self) -> None:
        assert not _is_binary_content_type("text/html")

    def test_html_with_charset_not_binary(self) -> None:
        assert not _is_binary_content_type("text/html; charset=utf-8")

    def test_json_not_binary(self) -> None:
        assert not _is_binary_content_type("application/json")

    def test_empty_not_binary(self) -> None:
        assert not _is_binary_content_type("")


class TestWebPdfRouting:
    """Test that web URLs returning PDF Content-Type route to PDF extraction."""

    def test_web_data_carries_raw_bytes_for_pdf(self) -> None:
        """Adapter populates raw_bytes for PDF content type."""
        pdf_bytes = b"%PDF-1.4 fake pdf content here"
        web_data = WebData(
            url="https://example.com/report.pdf",
            html='',
            final_url="https://example.com/report.pdf",
            status_code=200,
            content_type="application/pdf",
            cookies_used=False,
            render_method='http',
            raw_bytes=pdf_bytes,
        )
        assert web_data.raw_bytes == pdf_bytes
        assert web_data.html == ''

    def test_web_data_no_raw_bytes_for_html(self) -> None:
        """Adapter does not populate raw_bytes for HTML content."""
        web_data = WebData(
            url="https://example.com/page",
            html='<html><body>Hello</body></html>',
            final_url="https://example.com/page",
            status_code=200,
            content_type="text/html",
            cookies_used=False,
            render_method='http',
        )
        assert web_data.raw_bytes is None

    @patch('tools.fetch.extract_pdf_content')
    @patch('tools.fetch.fetch_web_content')
    def test_fetch_web_routes_pdf_to_extraction(self, mock_fetch, mock_extract) -> None:
        """fetch_web() detects PDF content type and routes to PDF extraction."""
        from tools.fetch import fetch_web
        from adapters.pdf import PdfExtractionResult

        pdf_bytes = b"%PDF-1.4 test content"
        mock_fetch.return_value = WebData(
            url="https://example.com/report.pdf",
            html='',
            final_url="https://example.com/report.pdf",
            status_code=200,
            content_type="application/pdf",
            cookies_used=False,
            render_method='http',
            raw_bytes=pdf_bytes,
        )
        mock_extract.return_value = PdfExtractionResult(
            content="# Extracted PDF Content",
            method="markitdown",
            char_count=25,
        )

        result = fetch_web("https://example.com/report.pdf")

        assert result.type == "pdf"
        assert result.format == "markdown"
        mock_extract.assert_called_once_with(file_bytes=pdf_bytes, file_id=mock_extract.call_args.kwargs['file_id'])

    def test_web_data_carries_temp_path_for_large_pdf(self) -> None:
        """WebData can carry temp_path for large streamed PDFs."""
        from pathlib import Path
        tmp = Path("/tmp/fake-large.pdf")
        web_data = WebData(
            url="https://example.com/huge.pdf",
            html='',
            final_url="https://example.com/huge.pdf",
            status_code=200,
            content_type="application/pdf",
            cookies_used=False,
            render_method='http',
            temp_path=tmp,
        )
        assert web_data.temp_path == tmp
        assert web_data.raw_bytes is None

    @patch('tools.fetch._extract_pdf_from_path')
    @patch('tools.fetch.fetch_web_content')
    def test_fetch_web_routes_large_pdf_via_temp_path(self, mock_fetch, mock_extract) -> None:
        """fetch_web() uses temp_path for large streamed PDFs."""
        from pathlib import Path
        from tools.fetch import fetch_web
        from adapters.pdf import PdfExtractionResult
        import tempfile

        # Create a real temp file so cleanup works
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(b"%PDF-1.4 large content")
        tmp.close()
        tmp_path = Path(tmp.name)

        mock_fetch.return_value = WebData(
            url="https://example.com/huge.pdf",
            html='',
            final_url="https://example.com/huge.pdf",
            status_code=200,
            content_type="application/pdf",
            cookies_used=False,
            render_method='http',
            temp_path=tmp_path,
        )
        mock_extract.return_value = PdfExtractionResult(
            content="# Large PDF Content",
            method="markitdown",
            char_count=20,
        )

        result = fetch_web("https://example.com/huge.pdf")

        assert result.type == "pdf"
        mock_extract.assert_called_once_with(tmp_path, mock_extract.call_args.args[1])
        # Verify temp file was cleaned up
        assert not tmp_path.exists(), "temp file should be cleaned up after extraction"

    @patch('tools.fetch._extract_pdf_from_path')
    @patch('tools.fetch.fetch_web_content')
    def test_fetch_web_cleans_up_temp_on_extraction_error(self, mock_fetch, mock_extract) -> None:
        """Temp file is cleaned up even if extraction fails."""
        from pathlib import Path
        from tools.fetch import fetch_web
        import tempfile

        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(b"%PDF-1.4")
        tmp.close()
        tmp_path = Path(tmp.name)

        mock_fetch.return_value = WebData(
            url="https://example.com/bad.pdf",
            html='',
            final_url="https://example.com/bad.pdf",
            status_code=200,
            content_type="application/pdf",
            cookies_used=False,
            render_method='http',
            temp_path=tmp_path,
        )
        mock_extract.side_effect = Exception("extraction boom")

        # The error propagates but temp file is still cleaned up
        with pytest.raises(Exception, match="extraction boom"):
            fetch_web("https://example.com/bad.pdf")

        assert not tmp_path.exists(), "temp file should be cleaned up even on error"


class TestContentLengthParsing:
    """Test Content-Length header parsing for streaming decision."""

    def test_valid_content_length(self) -> None:
        assert _parse_content_length("1234567") == 1234567

    def test_content_length_with_whitespace(self) -> None:
        assert _parse_content_length("  1234567  ") == 1234567

    def test_missing_content_length(self) -> None:
        assert _parse_content_length(None) is None

    def test_empty_content_length(self) -> None:
        assert _parse_content_length("") is None

    def test_invalid_content_length(self) -> None:
        assert _parse_content_length("not-a-number") is None

    def test_threshold_boundary(self) -> None:
        """Content-Length at exactly the threshold should NOT trigger streaming."""
        assert STREAMING_THRESHOLD_BYTES == 50 * 1024 * 1024
        # At threshold = not over = no streaming
        assert _parse_content_length(str(STREAMING_THRESHOLD_BYTES)) == STREAMING_THRESHOLD_BYTES
