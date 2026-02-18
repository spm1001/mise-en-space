"""
Tests for web content extraction.

Tests the web adapter and extractor with mocked HTTP responses.
"""

import subprocess

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.parse import urlparse

import httpx

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
    fetch_web_content,
    _detect_auth_required,
    _detect_captcha,
    _needs_browser_rendering,
    _is_passe_available,
    _fetch_with_passe,
    _stream_binary_to_temp,
    _is_binary_content_type,
    _parse_content_length,
    STREAMING_THRESHOLD_BYTES,
)


from tests.helpers import wire_httpx_client as _wire_httpx_client


@pytest.fixture(autouse=True)
def _mock_web_deposit(tmp_path):
    """Prevent web fetch tests from depositing into the MCP server's directory.

    All tests in this file test routing/extraction, not deposit behaviour.
    The workspace layer now requires an explicit base_path (no cwd fallback),
    so we mock get_deposit_folder to return tmp_path for every test.
    """
    with patch('tools.fetch.web.get_deposit_folder', return_value=tmp_path):
        yield


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

    def test_nextjs_in_long_content(self) -> None:
        """Long content (>500 chars) still detected via framework pattern."""
        html = "<html><body>" + "x" * 600 + '<script id="__NEXT_DATA__">{}</script></body></html>'
        assert _needs_browser_rendering(html)

    def test_ssr_hydration_pattern(self) -> None:
        html = "<html><body>" + "x" * 600 + "<script>window.__INITIAL_STATE__={}</script></body></html>"
        assert _needs_browser_rendering(html)

    def test_static_content(self) -> None:
        html = "<html><body>" + "x" * 1000 + "</body></html>"
        assert not _needs_browser_rendering(html)

    def test_react_vite_spa_big_head(self) -> None:
        """entire.io pattern: large HTML (big <head>), but empty <body>.
        Must exceed 500 chars to test tier 2 (body heuristic), not tier 1."""
        html = (
            "<html><head>"
            + '<meta name="description" content="' + "x" * 500 + '">'
            + '<link rel="stylesheet" href="/assets/index.css">'
            + '<script type="module" src="/assets/index.js"></script>'
            + "</head><body>"
            + '<div id="root"></div>'
            + "</body></html>"
        )
        assert len(html) > 500  # Passes tier 1 — detected by tier 2
        assert _needs_browser_rendering(html)

    def test_empty_body_with_scripts(self) -> None:
        """Body has only div + script tags, no visible text."""
        html = (
            "<html><head><title>App</title>" + "x" * 500 + "</head>"
            "<body><div id='app'></div><script src='bundle.js'></script></body></html>"
        )
        assert _needs_browser_rendering(html)

    def test_body_with_loading_text(self) -> None:
        """Minimal loading text under 100-char threshold."""
        html = (
            "<html><head>" + "x" * 500 + "</head>"
            "<body><div id='root'>Loading...</div></body></html>"
        )
        assert _needs_browser_rendering(html)

    def test_body_with_sufficient_text(self) -> None:
        """Real content in body — should NOT trigger."""
        html = (
            "<html><head><title>Blog</title></head>"
            "<body><article>" + "This is a real article with enough content. " * 10 + "</article></body></html>"
        )
        assert not _needs_browser_rendering(html)

    def test_react_root_pattern(self) -> None:
        """React root div detected via framework pattern."""
        html = "<html><body>" + "x" * 600 + '<div id="root"></div></body></html>'
        assert _needs_browser_rendering(html)

    def test_vue_app_pattern(self) -> None:
        """Vue app div detected via framework pattern."""
        html = "<html><body>" + "x" * 600 + '<div id="app"></div></body></html>'
        assert _needs_browser_rendering(html)

    def test_no_body_tag(self) -> None:
        """Edge case: no <body> tag doesn't crash body heuristic."""
        html = "<html><head>" + "x" * 600 + "</head></html>"
        # No body tag → body heuristic doesn't match → falls through to patterns
        # No patterns match → False
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

    def test_docx_content_type(self) -> None:
        assert _is_binary_content_type(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    def test_xlsx_content_type(self) -> None:
        assert _is_binary_content_type(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_pptx_content_type(self) -> None:
        assert _is_binary_content_type(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )

    def test_docx_with_charset(self) -> None:
        assert _is_binary_content_type(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document; charset=utf-8"
        )


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

    @patch('tools.fetch.web.extract_pdf_content')
    @patch('tools.fetch.web.fetch_web_content')
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

    @patch('tools.fetch.web.extract_pdf_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_fetch_web_routes_large_pdf_via_temp_path(self, mock_fetch, mock_extract) -> None:
        """fetch_web() uses temp_path for large streamed PDFs."""
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
        mock_extract.assert_called_once()
        assert mock_extract.call_args.kwargs["file_path"] == tmp_path
        assert "file_bytes" not in mock_extract.call_args.kwargs
        # Verify temp file was cleaned up
        assert not tmp_path.exists(), "temp file should be cleaned up after extraction"

    @patch('tools.fetch.web.extract_pdf_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_fetch_web_cleans_up_temp_on_extraction_error(self, mock_fetch, mock_extract) -> None:
        """Temp file is cleaned up even if extraction fails."""
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


class TestWebOfficeRouting:
    """Test that web URLs returning Office Content-Types route to Office extraction."""

    DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

    def test_web_data_carries_raw_bytes_for_docx(self) -> None:
        """Adapter populates raw_bytes for DOCX content type."""
        docx_bytes = b"PK\x03\x04 fake docx"
        web_data = WebData(
            url="https://example.com/report.docx",
            html='',
            final_url="https://example.com/report.docx",
            status_code=200,
            content_type=self.DOCX_MIME,
            cookies_used=False,
            render_method='http',
            raw_bytes=docx_bytes,
        )
        assert web_data.raw_bytes == docx_bytes
        assert web_data.html == ''

    @patch('tools.fetch.web.extract_office_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_fetch_web_routes_docx_to_extraction(self, mock_fetch, mock_extract) -> None:
        """fetch_web() detects DOCX content type and routes to Office extraction."""
        from tools.fetch import fetch_web
        from adapters.office import OfficeExtractionResult

        docx_bytes = b"PK\x03\x04 fake docx"
        mock_fetch.return_value = WebData(
            url="https://example.com/report.docx",
            html='',
            final_url="https://example.com/report.docx",
            status_code=200,
            content_type=self.DOCX_MIME,
            cookies_used=False,
            render_method='http',
            raw_bytes=docx_bytes,
        )
        mock_extract.return_value = OfficeExtractionResult(
            content="# Report Content",
            source_type="docx",
            export_format="markdown",
            extension="md",
        )

        result = fetch_web("https://example.com/report.docx")

        assert result.type == "docx"
        assert result.format == "markdown"
        assert result.metadata["title"] == "report"
        mock_extract.assert_called_once()
        call_args = mock_extract.call_args
        assert call_args.args[0] == "docx"
        assert call_args.kwargs["file_bytes"] == docx_bytes

    @patch('tools.fetch.web.extract_office_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_fetch_web_routes_xlsx_to_extraction(self, mock_fetch, mock_extract) -> None:
        """fetch_web() detects XLSX content type and routes to Office extraction."""
        from tools.fetch import fetch_web
        from adapters.office import OfficeExtractionResult

        xlsx_bytes = b"PK\x03\x04 fake xlsx"
        mock_fetch.return_value = WebData(
            url="https://example.com/data.xlsx",
            html='',
            final_url="https://example.com/data.xlsx",
            status_code=200,
            content_type=self.XLSX_MIME,
            cookies_used=False,
            render_method='http',
            raw_bytes=xlsx_bytes,
        )
        mock_extract.return_value = OfficeExtractionResult(
            content="Name,Value\nAlice,100",
            source_type="xlsx",
            export_format="csv",
            extension="csv",
        )

        result = fetch_web("https://example.com/data.xlsx")

        assert result.type == "xlsx"
        assert result.format == "csv"
        assert result.metadata["title"] == "data"
        mock_extract.assert_called_once()
        assert mock_extract.call_args.args[0] == "xlsx"

    @patch('tools.fetch.web.extract_office_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_fetch_web_routes_pptx_to_extraction(self, mock_fetch, mock_extract) -> None:
        """fetch_web() detects PPTX content type and routes to Office extraction."""
        from tools.fetch import fetch_web
        from adapters.office import OfficeExtractionResult

        pptx_bytes = b"PK\x03\x04 fake pptx"
        mock_fetch.return_value = WebData(
            url="https://example.com/slides.pptx",
            html='',
            final_url="https://example.com/slides.pptx",
            status_code=200,
            content_type=self.PPTX_MIME,
            cookies_used=False,
            render_method='http',
            raw_bytes=pptx_bytes,
        )
        mock_extract.return_value = OfficeExtractionResult(
            content="Slide 1: Title",
            source_type="pptx",
            export_format="plain",
            extension="txt",
        )

        result = fetch_web("https://example.com/slides.pptx")

        assert result.type == "pptx"
        assert result.format == "markdown"  # non-xlsx → markdown
        assert result.metadata["title"] == "slides"

    @patch('tools.fetch.web.extract_office_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_fetch_web_routes_large_office_via_temp_path(self, mock_fetch, mock_extract) -> None:
        """fetch_web() uses temp_path for large streamed Office files."""
        from tools.fetch import fetch_web
        from adapters.office import OfficeExtractionResult
        import tempfile

        # Create a real temp file so cleanup works
        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp.write(b"PK\x03\x04 large docx")
        tmp.close()
        tmp_path = Path(tmp.name)

        mock_fetch.return_value = WebData(
            url="https://example.com/huge-report.docx",
            html='',
            final_url="https://example.com/huge-report.docx",
            status_code=200,
            content_type=self.DOCX_MIME,
            cookies_used=False,
            render_method='http',
            temp_path=tmp_path,
        )
        mock_extract.return_value = OfficeExtractionResult(
            content="# Huge Report",
            source_type="docx",
            export_format="markdown",
            extension="md",
        )

        result = fetch_web("https://example.com/huge-report.docx")

        assert result.type == "docx"
        mock_extract.assert_called_once()
        assert mock_extract.call_args.kwargs["file_path"] == tmp_path
        assert "file_bytes" not in mock_extract.call_args.kwargs
        # Verify temp file was cleaned up
        assert not tmp_path.exists(), "temp file should be cleaned up after extraction"

    @patch('tools.fetch.web.extract_office_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_fetch_web_cleans_up_office_temp_on_error(self, mock_fetch, mock_extract) -> None:
        """Temp file is cleaned up even if Office extraction fails."""
        from tools.fetch import fetch_web
        import tempfile

        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        tmp.write(b"PK\x03\x04")
        tmp.close()
        tmp_path = Path(tmp.name)

        mock_fetch.return_value = WebData(
            url="https://example.com/bad.xlsx",
            html='',
            final_url="https://example.com/bad.xlsx",
            status_code=200,
            content_type=self.XLSX_MIME,
            cookies_used=False,
            render_method='http',
            temp_path=tmp_path,
        )
        mock_extract.side_effect = Exception("conversion boom")

        with pytest.raises(Exception, match="conversion boom"):
            fetch_web("https://example.com/bad.xlsx")

        assert not tmp_path.exists(), "temp file should be cleaned up even on error"

    @patch('tools.fetch.web.extract_office_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_fetch_web_office_no_content_raises(self, mock_fetch, mock_extract) -> None:
        """fetch_web() raises MiseError when Office response has neither bytes nor temp_path."""
        from tools.fetch import fetch_web

        mock_fetch.return_value = WebData(
            url="https://example.com/empty.docx",
            html='',
            final_url="https://example.com/empty.docx",
            status_code=200,
            content_type=self.DOCX_MIME,
            cookies_used=False,
            render_method='http',
            # No raw_bytes, no temp_path
        )

        with pytest.raises(MiseError, match="No Office content received"):
            fetch_web("https://example.com/empty.docx")

    @patch('tools.fetch.web.extract_office_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_fetch_web_office_content_type_with_charset(self, mock_fetch, mock_extract) -> None:
        """Office Content-Type with charset parameter still routes correctly."""
        from tools.fetch import fetch_web
        from adapters.office import OfficeExtractionResult

        mock_fetch.return_value = WebData(
            url="https://example.com/report.docx",
            html='',
            final_url="https://example.com/report.docx",
            status_code=200,
            content_type=f"{self.DOCX_MIME}; charset=utf-8",
            cookies_used=False,
            render_method='http',
            raw_bytes=b"PK\x03\x04 docx",
        )
        mock_extract.return_value = OfficeExtractionResult(
            content="# Report",
            source_type="docx",
            export_format="markdown",
            extension="md",
        )

        result = fetch_web("https://example.com/report.docx")

        assert result.type == "docx"


class TestPdfContentTypeMismatch:
    """Test that HTML masquerading as PDF gets a clear error."""

    @patch('tools.fetch.web.fetch_web_content')
    def test_html_bytes_with_pdf_content_type_raises(self, mock_fetch) -> None:
        """CDN returning HTML with application/pdf Content-Type gets actionable error."""
        from tools.fetch import fetch_web

        html_bytes = b"<html><body>Access Denied</body></html>"
        mock_fetch.return_value = WebData(
            url="https://cdn.example.com/report.pdf",
            html='',
            final_url="https://cdn.example.com/report.pdf",
            status_code=200,
            content_type="application/pdf",
            cookies_used=False,
            render_method='http',
            raw_bytes=html_bytes,
        )

        with pytest.raises(MiseError) as exc_info:
            fetch_web("https://cdn.example.com/report.pdf")

        assert exc_info.value.kind == ErrorKind.EXTRACTION_FAILED
        assert "not PDF" in exc_info.value.message
        assert "cdn.example.com" in exc_info.value.message

    @patch('tools.fetch.web.fetch_web_content')
    def test_empty_bytes_with_pdf_content_type_raises(self, mock_fetch) -> None:
        """Empty response with PDF Content-Type gets clear error."""
        from tools.fetch import fetch_web

        mock_fetch.return_value = WebData(
            url="https://example.com/empty.pdf",
            html='',
            final_url="https://example.com/empty.pdf",
            status_code=200,
            content_type="application/pdf",
            cookies_used=False,
            render_method='http',
            raw_bytes=b"",  # falsy — hits "no content" branch, not magic bytes
        )

        with pytest.raises(MiseError) as exc_info:
            fetch_web("https://example.com/empty.pdf")

        assert exc_info.value.kind == ErrorKind.EXTRACTION_FAILED
        assert "No PDF content" in exc_info.value.message

    @patch('tools.fetch.web.fetch_web_content')
    def test_html_in_large_pdf_temp_path_raises(self, mock_fetch) -> None:
        """Large file with HTML content but PDF Content-Type gets caught."""
        from tools.fetch import fetch_web
        import tempfile

        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(b"<!DOCTYPE html><html><body>Error</body></html>")
        tmp.close()
        tmp_path = Path(tmp.name)

        mock_fetch.return_value = WebData(
            url="https://cdn.example.com/big-report.pdf",
            html='',
            final_url="https://cdn.example.com/big-report.pdf",
            status_code=200,
            content_type="application/pdf",
            cookies_used=False,
            render_method='http',
            temp_path=tmp_path,
        )

        try:
            with pytest.raises(MiseError) as exc_info:
                fetch_web("https://cdn.example.com/big-report.pdf")

            assert "not PDF" in exc_info.value.message
        finally:
            tmp_path.unlink(missing_ok=True)

    @patch('tools.fetch.web.extract_pdf_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_real_pdf_bytes_pass_through(self, mock_fetch, mock_extract) -> None:
        """Actual PDF bytes pass the magic check and reach extraction."""
        from tools.fetch import fetch_web
        from adapters.pdf import PdfExtractionResult

        mock_fetch.return_value = WebData(
            url="https://example.com/real.pdf",
            html='',
            final_url="https://example.com/real.pdf",
            status_code=200,
            content_type="application/pdf",
            cookies_used=False,
            render_method='http',
            raw_bytes=b"%PDF-1.4 real pdf content",
        )
        mock_extract.return_value = PdfExtractionResult(
            content="# Real PDF", method="markitdown", char_count=10,
        )

        result = fetch_web("https://example.com/real.pdf")
        assert result.type == "pdf"
        mock_extract.assert_called_once()


class TestIsRawText:
    """Test raw text detection by Content-Type and URL extension."""

    # -- Content-Type detection --

    def test_text_plain(self) -> None:
        assert _is_raw_text("text/plain", "https://example.com/file")

    def test_application_json(self) -> None:
        assert _is_raw_text("application/json", "https://api.example.com/data")

    def test_text_markdown(self) -> None:
        assert _is_raw_text("text/markdown", "https://example.com/readme")

    def test_text_csv(self) -> None:
        assert _is_raw_text("text/csv", "https://example.com/data")

    def test_application_xml(self) -> None:
        assert _is_raw_text("application/xml", "https://example.com/feed")

    def test_text_yaml(self) -> None:
        assert _is_raw_text("text/yaml", "https://example.com/config")

    def test_javascript(self) -> None:
        assert _is_raw_text("application/javascript", "https://example.com/bundle")

    def test_content_type_with_charset(self) -> None:
        assert _is_raw_text("application/json; charset=utf-8", "https://example.com/api")

    def test_content_type_case_insensitive(self) -> None:
        assert _is_raw_text("Application/JSON", "https://example.com/api")

    def test_html_not_raw(self) -> None:
        assert not _is_raw_text("text/html", "https://example.com/page")

    def test_pdf_not_raw(self) -> None:
        assert not _is_raw_text("application/pdf", "https://example.com/doc.pdf")

    # -- URL extension detection --

    def test_py_extension(self) -> None:
        assert _is_raw_text("text/html", "https://raw.githubusercontent.com/user/repo/main/script.py")

    def test_json_extension(self) -> None:
        assert _is_raw_text("text/html", "https://example.com/config.json")

    def test_md_extension(self) -> None:
        assert _is_raw_text("text/html", "https://raw.githubusercontent.com/user/repo/main/README.md")

    def test_yaml_extension(self) -> None:
        assert _is_raw_text("text/html", "https://example.com/config.yaml")

    def test_toml_extension(self) -> None:
        assert _is_raw_text("text/html", "https://example.com/pyproject.toml")

    def test_sh_extension(self) -> None:
        assert _is_raw_text("text/html", "https://example.com/install.sh")

    def test_extension_with_query_params(self) -> None:
        """Query params don't break extension detection."""
        assert _is_raw_text("text/html", "https://example.com/script.py?v=2")

    def test_html_extension_not_raw(self) -> None:
        assert not _is_raw_text("text/html", "https://example.com/page.html")

    def test_no_extension_not_raw(self) -> None:
        assert not _is_raw_text("text/html", "https://example.com/page")

    def test_unknown_extension_not_raw(self) -> None:
        assert not _is_raw_text("text/html", "https://example.com/image.png")


class TestGetLanguageFromUrl:
    """Test language hint extraction from URL extension."""

    def test_python(self) -> None:
        assert _get_language_from_url("https://example.com/script.py") == "python"

    def test_javascript(self) -> None:
        assert _get_language_from_url("https://example.com/app.js") == "javascript"

    def test_typescript(self) -> None:
        assert _get_language_from_url("https://example.com/app.ts") == "typescript"

    def test_rust(self) -> None:
        assert _get_language_from_url("https://example.com/main.rs") == "rust"

    def test_yaml(self) -> None:
        assert _get_language_from_url("https://example.com/config.yaml") == "yaml"

    def test_yml_variant(self) -> None:
        assert _get_language_from_url("https://example.com/config.yml") == "yaml"

    def test_bash_from_sh(self) -> None:
        assert _get_language_from_url("https://example.com/install.sh") == "bash"

    def test_no_match(self) -> None:
        assert _get_language_from_url("https://example.com/page") == ""

    def test_unknown_extension(self) -> None:
        assert _get_language_from_url("https://example.com/image.png") == ""


class TestFormatRawText:
    """Test raw text formatting for different content types."""

    def test_markdown_passthrough_with_heading(self) -> None:
        """Markdown with heading passes through unchanged."""
        content = "# My Doc\n\nSome content here."
        result = _format_raw_text(content, "https://example.com/README.md", "text/markdown")
        assert result == content

    def test_markdown_adds_title_when_no_heading(self) -> None:
        """Markdown without heading gets filename as title."""
        content = "Some content without a heading."
        result = _format_raw_text(content, "https://example.com/notes.md", "text/markdown")
        assert result == "# notes.md\n\nSome content without a heading."

    def test_json_pretty_printed(self) -> None:
        """Compact JSON gets pretty-printed in a code fence."""
        content = '{"name":"test","value":42}'
        result = _format_raw_text(content, "https://api.example.com/data.json", "application/json")
        assert "```json" in result
        assert '"name": "test"' in result
        assert "data.json" in result

    def test_json_invalid_kept_as_is(self) -> None:
        """Invalid JSON kept verbatim in a code fence."""
        content = '{broken json'
        result = _format_raw_text(content, "https://example.com/data.json", "application/json")
        assert "```json" in result
        assert "{broken json" in result

    def test_json_by_content_type_not_extension(self) -> None:
        """JSON detected by Content-Type even without .json extension."""
        content = '{"key": "value"}'
        result = _format_raw_text(content, "https://api.example.com/v1/data", "application/json")
        assert "```json" in result

    def test_python_code_fenced(self) -> None:
        """Python file wrapped in code fence with language hint."""
        content = 'def hello():\n    print("hi")'
        result = _format_raw_text(content, "https://example.com/script.py", "text/plain")
        assert "```python" in result
        assert "script.py" in result
        assert content in result

    def test_rust_code_fenced(self) -> None:
        content = 'fn main() { println!("hello"); }'
        result = _format_raw_text(content, "https://example.com/main.rs", "text/plain")
        assert "```rust" in result

    def test_plain_text_no_fence(self) -> None:
        """Plain text with unknown extension gets title but no code fence."""
        content = "Just some text content."
        result = _format_raw_text(content, "https://example.com/notes.txt", "text/plain")
        assert "notes.txt" in result
        assert "```" not in result
        assert content in result

    def test_filename_extracted_from_url(self) -> None:
        """Filename extracted from last URL path segment."""
        result = _format_raw_text("x", "https://example.com/path/to/config.yaml", "text/yaml")
        assert "config.yaml" in result

    def test_empty_path_fallback(self) -> None:
        """Empty URL path uses fallback filename."""
        result = _format_raw_text("x", "https://example.com/", "text/plain")
        assert len(result) > 0


class TestExtractWebContentRawText:
    """Test extract_web_content raw text branch end-to-end."""

    def test_json_api_response(self) -> None:
        """JSON content goes through raw text path, not trafilatura."""
        data = WebData(
            url="https://api.example.com/v1/users",
            html='{"users": [{"id": 1}]}',
            final_url="https://api.example.com/v1/users",
            status_code=200,
            content_type="application/json",
            cookies_used=False,
            render_method="http",
            warnings=[],
        )
        content = extract_web_content(data)
        assert "```json" in content
        assert '"users"' in content
        assert any("Raw text" in w for w in data.warnings)

    def test_github_raw_python(self) -> None:
        """GitHub raw URLs detected by extension, fenced as Python."""
        data = WebData(
            url="https://raw.githubusercontent.com/user/repo/main/app.py",
            html='import sys\nprint(sys.argv)',
            final_url="https://raw.githubusercontent.com/user/repo/main/app.py",
            status_code=200,
            content_type="text/plain",
            cookies_used=False,
            render_method="http",
            warnings=[],
        )
        content = extract_web_content(data)
        assert "```python" in content
        assert "import sys" in content

    def test_markdown_file_passthrough(self) -> None:
        """Markdown file returned as-is (already markdown)."""
        md = "# README\n\nThis is a project."
        data = WebData(
            url="https://raw.githubusercontent.com/user/repo/main/README.md",
            html=md,
            final_url="https://raw.githubusercontent.com/user/repo/main/README.md",
            status_code=200,
            content_type="text/plain",
            cookies_used=False,
            render_method="http",
            warnings=[],
        )
        content = extract_web_content(data)
        assert content == md


# ============================================================================
# HELPERS: mock httpx response
# ============================================================================


def _mock_response(
    *,
    status_code: int = 200,
    text: str = "<html><body>Hello world content that is long enough to avoid JS detection " + "x" * 500 + "</body></html>",
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
    url: str = "https://example.com",
) -> MagicMock:
    """Build a mock httpx.Response with the right attributes."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.content = content if content is not None else text.encode()
    resp.url = httpx.URL(url)
    default_headers = {"content-type": "text/html; charset=utf-8"}
    if headers:
        default_headers.update(headers)
    resp.headers = default_headers
    return resp


# ============================================================================
# fetch_web_content ORCHESTRATOR TESTS
# ============================================================================


class TestFetchWebContentValidation:
    """URL validation before any HTTP calls."""

    def test_invalid_url_raises(self) -> None:
        with pytest.raises(MiseError) as exc_info:
            fetch_web_content("not-a-url")
        assert exc_info.value.kind == ErrorKind.INVALID_INPUT

    def test_drive_id_rejected(self) -> None:
        with pytest.raises(MiseError) as exc_info:
            fetch_web_content("1a2b3c4d5e6f")
        assert exc_info.value.kind == ErrorKind.INVALID_INPUT


class TestFetchWebContentHTTPErrors:
    """HTTP-level error handling in fetch_web_content."""

    @patch("adapters.web.httpx.Client")
    def test_timeout_raises(self, mock_client_cls) -> None:
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.side_effect = httpx.TimeoutException("timed out")

        with pytest.raises(MiseError) as exc_info:
            fetch_web_content("https://example.com/slow")
        assert exc_info.value.kind == ErrorKind.TIMEOUT

    @patch("adapters.web.httpx.Client")
    def test_connect_error_raises(self, mock_client_cls) -> None:
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.side_effect = httpx.ConnectError("refused")

        with pytest.raises(MiseError) as exc_info:
            fetch_web_content("https://down.example.com")
        assert exc_info.value.kind == ErrorKind.NETWORK_ERROR

    @patch("adapters.web.httpx.Client")
    def test_request_error_raises(self, mock_client_cls) -> None:
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.side_effect = httpx.RequestError("DNS fail")

        with pytest.raises(MiseError) as exc_info:
            fetch_web_content("https://example.com")
        assert exc_info.value.kind == ErrorKind.NETWORK_ERROR

    @patch("adapters.web.httpx.Client")
    def test_redirect_loop_raises(self, mock_client_cls) -> None:
        """Redirect loop produces clear error, not generic RequestError."""
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.side_effect = httpx.TooManyRedirects(
            "Exceeded max redirects", request=MagicMock()
        )

        with pytest.raises(MiseError) as exc_info:
            fetch_web_content("https://loop.example.com/a")
        assert exc_info.value.kind == ErrorKind.NETWORK_ERROR
        assert "redirect" in exc_info.value.message.lower()


class TestFetchWebContentStatusCodes:
    """HTTP status code handling."""

    @patch("adapters.web.httpx.Client")
    def test_429_raises_rate_limited(self, mock_client_cls) -> None:
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.return_value = _mock_response(status_code=429)

        with pytest.raises(MiseError) as exc_info:
            fetch_web_content("https://api.example.com/data")
        assert exc_info.value.kind == ErrorKind.RATE_LIMITED
        assert exc_info.value.retryable is True

    @patch("adapters.web.httpx.Client")
    def test_500_raises_network_error(self, mock_client_cls) -> None:
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.return_value = _mock_response(status_code=500)

        with pytest.raises(MiseError) as exc_info:
            fetch_web_content("https://example.com/broken")
        assert exc_info.value.kind == ErrorKind.NETWORK_ERROR

    @patch("adapters.web.httpx.Client")
    def test_502_raises_network_error(self, mock_client_cls) -> None:
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.return_value = _mock_response(status_code=502)

        with pytest.raises(MiseError) as exc_info:
            fetch_web_content("https://example.com")
        assert exc_info.value.kind == ErrorKind.NETWORK_ERROR

    @patch("adapters.web.httpx.Client")
    def test_404_raises_not_found(self, mock_client_cls) -> None:
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.return_value = _mock_response(status_code=404)

        with pytest.raises(MiseError) as exc_info:
            fetch_web_content("https://example.com/missing")
        assert exc_info.value.kind == ErrorKind.NOT_FOUND


class TestFetchWebContentBinary:
    """Binary content detection and routing."""

    @patch("adapters.web.httpx.Client")
    def test_pdf_response_captures_raw_bytes(self, mock_client_cls) -> None:
        """PDF Content-Type → raw_bytes populated, html empty."""
        mock_client = _wire_httpx_client(mock_client_cls)

        pdf_bytes = b"%PDF-1.4 content here"
        mock_client.get.return_value = _mock_response(
            status_code=200,
            content=pdf_bytes,
            headers={"content-type": "application/pdf"},
        )

        result = fetch_web_content("https://example.com/report.pdf")

        assert result.raw_bytes == pdf_bytes
        assert result.html == ""
        assert result.render_method == "http"
        assert result.temp_path is None

    @patch("adapters.web._stream_binary_to_temp")
    @patch("adapters.web.httpx.Client")
    def test_large_pdf_streams_to_temp(self, mock_client_cls, mock_stream) -> None:
        """Binary over threshold → streams to temp file."""
        mock_client = _wire_httpx_client(mock_client_cls)

        big_size = str(STREAMING_THRESHOLD_BYTES + 1)
        mock_client.get.return_value = _mock_response(
            status_code=200,
            headers={
                "content-type": "application/pdf",
                "content-length": big_size,
            },
        )
        fake_tmp = Path("/tmp/fake-streamed.pdf")
        mock_stream.return_value = fake_tmp

        result = fetch_web_content("https://example.com/huge.pdf")

        assert result.temp_path == fake_tmp
        assert result.raw_bytes is None
        assert result.html == ""
        mock_stream.assert_called_once()
        assert any("streaming" in w.lower() for w in result.warnings)

    @patch("adapters.web.httpx.Client")
    def test_binary_without_content_length_loads_to_memory(
        self, mock_client_cls
    ) -> None:
        """Binary with no Content-Length → loads into memory (safe default)."""
        mock_client = _wire_httpx_client(mock_client_cls)

        mock_client.get.return_value = _mock_response(
            status_code=200,
            content=b"%PDF-1.4 small",
            headers={"content-type": "application/pdf"},
            # No content-length header
        )

        result = fetch_web_content("https://example.com/doc.pdf")

        assert result.raw_bytes == b"%PDF-1.4 small"
        assert result.temp_path is None

    @patch("adapters.web.httpx.Client")
    def test_docx_response_captures_raw_bytes(self, mock_client_cls) -> None:
        """DOCX Content-Type → raw_bytes populated."""
        mock_client = _wire_httpx_client(mock_client_cls)

        docx_bytes = b"PK\x03\x04 fake docx"
        mock_client.get.return_value = _mock_response(
            status_code=200,
            content=docx_bytes,
            headers={
                "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            },
        )

        result = fetch_web_content("https://example.com/report.docx")

        assert result.raw_bytes == docx_bytes
        assert result.html == ""


class TestFetchWebContentHTML:
    """HTML content path — CAPTCHA, auth, JS detection, size limits."""

    @patch("adapters.web.httpx.Client")
    def test_html_size_bomb_rejected(self, mock_client_cls) -> None:
        """HTML response claiming >10MB via Content-Length is rejected before loading."""
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.return_value = _mock_response(
            headers={"content-length": str(50 * 1024 * 1024)},  # 50 MB
        )

        with pytest.raises(MiseError) as exc_info:
            fetch_web_content("https://bomb.example.com/huge.html")
        assert exc_info.value.kind == ErrorKind.EXTRACTION_FAILED
        assert "too large" in exc_info.value.message.lower()

    @patch("adapters.web.httpx.Client")
    def test_captcha_raises(self, mock_client_cls) -> None:
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.return_value = _mock_response(
            text='<html><div class="cf-challenge">Checking your browser</div></html>',
        )

        with pytest.raises(MiseError) as exc_info:
            fetch_web_content("https://protected.example.com")
        assert exc_info.value.kind == ErrorKind.CAPTCHA

    @patch("adapters.web._is_passe_available", return_value=False)
    @patch("adapters.web.httpx.Client")
    def test_auth_401_raises_with_passe_hint(self, mock_client_cls, _passe) -> None:
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.return_value = _mock_response(status_code=401)

        with pytest.raises(MiseError) as exc_info:
            fetch_web_content("https://private.example.com")
        assert exc_info.value.kind == ErrorKind.AUTH_REQUIRED
        assert "passe" in str(exc_info.value.message).lower()

    @patch("adapters.web._fetch_with_passe")
    @patch("adapters.web._is_passe_available", return_value=True)
    @patch("adapters.web.httpx.Client")
    def test_auth_403_falls_back_to_passe(self, mock_client_cls, _passe, mock_passe_fetch) -> None:
        """403 with passe available → auto-fallback to browser."""
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.return_value = _mock_response(status_code=403)
        mock_passe_fetch.return_value = ("# Protected Content\n\nNow visible.", "https://private.example.com")

        result = fetch_web_content("https://private.example.com")

        assert result.render_method == "passe"
        assert result.pre_extracted_content == "# Protected Content\n\nNow visible."
        assert any("403" in w for w in result.warnings)
        assert any("browser" in w.lower() for w in result.warnings)

    @patch("adapters.web._is_passe_available", return_value=False)
    @patch("adapters.web.httpx.Client")
    def test_paywall_raises(self, mock_client_cls, _passe) -> None:
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.return_value = _mock_response(
            text="<html><body>" + "x" * 600 + " subscribe to continue reading</body></html>",
        )

        with pytest.raises(MiseError) as exc_info:
            fetch_web_content("https://news.example.com/article")
        assert exc_info.value.kind == ErrorKind.AUTH_REQUIRED

    @patch("adapters.web._is_passe_available", return_value=False)
    @patch("adapters.web.httpx.Client")
    def test_js_rendered_no_passe_warns(self, mock_client_cls, _passe) -> None:
        """JS-rendered content without passe returns HTML with warning."""
        mock_client = _wire_httpx_client(mock_client_cls)
        # Short content triggers JS detection
        mock_client.get.return_value = _mock_response(
            text='<html><script id="__NEXT_DATA__">{}</script><body>tiny</body></html>',
        )

        result = fetch_web_content("https://spa.example.com")

        assert result.render_method == "http"
        assert any("JS-rendered" in w for w in result.warnings)
        assert any("passe not available" in w for w in result.warnings)

    @patch("adapters.web._fetch_with_passe")
    @patch("adapters.web._is_passe_available", return_value=True)
    @patch("adapters.web.httpx.Client")
    def test_js_rendered_with_passe_falls_back(
        self, mock_client_cls, _passe, mock_passe_fetch
    ) -> None:
        """JS-rendered + passe available → falls back to passe, sets pre_extracted_content."""
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.return_value = _mock_response(
            text='<html><script id="__NEXT_DATA__">{}</script><body>tiny</body></html>',
        )
        mock_passe_fetch.return_value = ("# Full Rendered Content\n\nArticle text.", "https://spa.example.com")

        result = fetch_web_content("https://spa.example.com")

        assert result.render_method == "passe"
        assert result.cookies_used is True
        assert result.pre_extracted_content == "# Full Rendered Content\n\nArticle text."
        assert any("passe browser rendering" in w.lower() for w in result.warnings)
        mock_passe_fetch.assert_called_once_with("https://spa.example.com")

    @patch("adapters.web.httpx.Client")
    def test_normal_html_returns_webdata(self, mock_client_cls) -> None:
        """Normal static HTML → WebData with http render method."""
        mock_client = _wire_httpx_client(mock_client_cls)
        html = "<html><body>" + "Content " * 200 + "</body></html>"
        mock_client.get.return_value = _mock_response(text=html)

        result = fetch_web_content("https://example.com/article")

        assert result.render_method == "http"
        assert result.cookies_used is False
        assert result.status_code == 200
        assert result.html == html

    @patch("adapters.web.httpx.Client")
    def test_redirect_tracked_in_warnings(self, mock_client_cls) -> None:
        """Redirect from original URL to final URL generates a warning."""
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.return_value = _mock_response(
            url="https://www.example.com/article",  # Different from request URL
        )

        result = fetch_web_content("https://example.com/article")

        assert result.final_url == "https://www.example.com/article"
        assert any("Redirected" in w for w in result.warnings)

    @patch("adapters.web.httpx.Client")
    def test_no_redirect_no_warning(self, mock_client_cls) -> None:
        """Same final URL → no redirect warning."""
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.get.return_value = _mock_response(
            url="https://example.com/page",
        )

        result = fetch_web_content("https://example.com/page")

        assert not any("Redirected" in w for w in result.warnings)


class TestFetchWebContentBrowserPath:
    """Forced browser path (use_browser=True)."""

    @patch("adapters.web._is_passe_available", return_value=False)
    def test_browser_requested_no_passe_raises(self, _passe) -> None:
        with pytest.raises(MiseError) as exc_info:
            fetch_web_content("https://example.com", use_browser=True)
        assert exc_info.value.kind == ErrorKind.INVALID_INPUT
        assert "passe" in exc_info.value.message.lower()

    @patch("adapters.web._fetch_with_passe")
    @patch("adapters.web._is_passe_available", return_value=True)
    def test_browser_requested_with_passe(self, _passe, mock_passe_fetch) -> None:
        mock_passe_fetch.return_value = (
            "# Browser Rendered\n\nContent here.",
            "https://example.com",
        )

        result = fetch_web_content("https://example.com", use_browser=True)

        assert result.render_method == "passe"
        assert result.cookies_used is True
        assert result.status_code == 200
        assert result.pre_extracted_content == "# Browser Rendered\n\nContent here."
        mock_passe_fetch.assert_called_once_with("https://example.com")


# ============================================================================
# _is_passe_available
# ============================================================================


class TestIsPasseAvailable:
    """Test passe + Chrome Debug availability check."""

    @patch("adapters.web.urllib.request.urlopen")
    @patch("adapters.web.shutil.which", return_value="/usr/local/bin/passe")
    def test_available(self, mock_which, mock_urlopen) -> None:
        """Both passe on PATH and Chrome Debug on port 9222."""
        mock_urlopen.return_value.__enter__ = MagicMock()
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        assert _is_passe_available() is True

    @patch("adapters.web.shutil.which", return_value=None)
    def test_not_installed(self, mock_which) -> None:
        """passe binary not on PATH."""
        assert _is_passe_available() is False

    @patch("adapters.web.urllib.request.urlopen", side_effect=ConnectionRefusedError)
    @patch("adapters.web.shutil.which", return_value="/usr/local/bin/passe")
    def test_chrome_debug_not_running(self, mock_which, mock_urlopen) -> None:
        """passe installed but Chrome Debug not running."""
        assert _is_passe_available() is False


# ============================================================================
# _fetch_with_passe
# ============================================================================


class TestFetchWithPasse:
    """Test browser rendering via passe subprocess."""

    @patch("adapters.web.subprocess.run")
    def test_successful_render(self, mock_run) -> None:
        """passe run succeeds → returns (markdown, original_url)."""
        import tempfile, hashlib
        url = "https://example.com"
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        tmp_path = Path(tempfile.gettempdir()) / f"passe-{url_hash}.md"

        # Write fake extracted markdown to where _fetch_with_passe expects it
        tmp_path.write_text("# Rendered Content\n\nSome article text.")

        mock_run.side_effect = [
            MagicMock(returncode=0, stderr=""),  # passe run
        ]

        try:
            markdown, final_url = _fetch_with_passe(url)
            assert "Rendered Content" in markdown
            # final_url is original URL until passe adds it to run summary
            assert final_url == url
        finally:
            tmp_path.unlink(missing_ok=True)

    @patch("adapters.web.subprocess.run")
    def test_render_failure(self, mock_run) -> None:
        """Non-zero exit from passe run → NETWORK_ERROR."""
        mock_run.side_effect = [
            MagicMock(returncode=1, stderr="Connection refused"),
        ]

        with pytest.raises(MiseError) as exc_info:
            _fetch_with_passe("https://example.com")
        assert exc_info.value.kind == ErrorKind.NETWORK_ERROR

    @patch("adapters.web.subprocess.run")
    def test_empty_output_raises_extraction_failed(self, mock_run) -> None:
        """passe succeeds but produces empty/missing output → EXTRACTION_FAILED."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stderr=""),  # passe run succeeds
        ]
        # Don't write any file — tmp_path won't exist

        with pytest.raises(MiseError) as exc_info:
            _fetch_with_passe("https://example.com/empty")
        assert exc_info.value.kind == ErrorKind.EXTRACTION_FAILED

    @patch("adapters.web.subprocess.run", side_effect=subprocess.TimeoutExpired("passe", 45))
    def test_timeout_raises(self, mock_run) -> None:
        with pytest.raises(MiseError) as exc_info:
            _fetch_with_passe("https://example.com")
        assert exc_info.value.kind == ErrorKind.TIMEOUT

    @patch("adapters.web.subprocess.run", side_effect=FileNotFoundError)
    def test_not_installed_raises(self, mock_run) -> None:
        with pytest.raises(MiseError) as exc_info:
            _fetch_with_passe("https://example.com")
        assert exc_info.value.kind == ErrorKind.INVALID_INPUT

    @patch("adapters.web.subprocess.run")
    def test_final_url_is_original(self, mock_run) -> None:
        """final_url is always original URL (no eval call — passe closes its tab)."""
        import tempfile, hashlib
        url = "https://example.com/page"
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        tmp_path = Path(tempfile.gettempdir()) / f"passe-{url_hash}.md"

        tmp_path.write_text("# Content")

        mock_run.side_effect = [
            MagicMock(returncode=0, stderr=""),  # passe run (only call)
        ]

        try:
            markdown, final_url = _fetch_with_passe(url)
            assert final_url == url
            # Only one subprocess call — no eval
            assert mock_run.call_count == 1
        finally:
            tmp_path.unlink(missing_ok=True)


# ============================================================================
# _stream_binary_to_temp
# ============================================================================


class TestStreamBinaryToTemp:
    """Test streaming download to temp file."""

    @patch("adapters.web.httpx.Client")
    def test_streams_pdf_to_temp(self, mock_client_cls) -> None:
        """Streaming download creates temp file with correct suffix."""
        mock_client = _wire_httpx_client(mock_client_cls)

        mock_response = MagicMock()
        mock_response.iter_bytes.return_value = [b"%PDF-1.4 chunk1", b" chunk2"]
        mock_response.raise_for_status = MagicMock()

        # Mock the stream context manager
        mock_client.stream.return_value.__enter__ = MagicMock(return_value=mock_response)
        mock_client.stream.return_value.__exit__ = MagicMock(return_value=False)

        path = _stream_binary_to_temp("https://example.com/big.pdf", "application/pdf")

        try:
            assert path.exists()
            assert path.suffix == ".pdf"
            content = path.read_bytes()
            assert b"%PDF-1.4 chunk1 chunk2" == content
        finally:
            path.unlink(missing_ok=True)

    @patch("adapters.web.httpx.Client")
    def test_iter_bytes_error_cleans_up_temp(self, mock_client_cls) -> None:
        """If iter_bytes fails mid-write, temp file is cleaned up."""
        mock_client = _wire_httpx_client(mock_client_cls)

        def exploding_iter(*args, **kwargs):
            yield b"partial data"
            raise IOError("disk full")

        mock_response = MagicMock()
        mock_response.iter_bytes = exploding_iter
        mock_response.raise_for_status = MagicMock()
        mock_client.stream.return_value.__enter__ = MagicMock(return_value=mock_response)
        mock_client.stream.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(IOError, match="disk full"):
            _stream_binary_to_temp("https://example.com/big.pdf", "application/pdf")

    @patch("adapters.web.httpx.Client")
    def test_timeout_raises_mise_error(self, mock_client_cls) -> None:
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.stream.side_effect = httpx.TimeoutException("slow")

        with pytest.raises(MiseError) as exc_info:
            _stream_binary_to_temp("https://example.com/big.pdf", "application/pdf")
        assert exc_info.value.kind == ErrorKind.TIMEOUT

    @patch("adapters.web.httpx.Client")
    def test_http_error_raises_mise_error(self, mock_client_cls) -> None:
        mock_client = _wire_httpx_client(mock_client_cls)

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403", request=MagicMock(), response=MagicMock(status_code=403)
        )
        mock_client.stream.return_value.__enter__ = MagicMock(return_value=mock_response)
        mock_client.stream.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(MiseError) as exc_info:
            _stream_binary_to_temp("https://example.com/big.pdf", "application/pdf")
        assert exc_info.value.kind == ErrorKind.NETWORK_ERROR

    @patch("adapters.web.httpx.Client")
    def test_request_error_raises_mise_error(self, mock_client_cls) -> None:
        mock_client = _wire_httpx_client(mock_client_cls)
        mock_client.stream.side_effect = httpx.RequestError("DNS fail")

        with pytest.raises(MiseError) as exc_info:
            _stream_binary_to_temp("https://example.com/big.pdf", "application/pdf")
        assert exc_info.value.kind == ErrorKind.NETWORK_ERROR

    @patch("adapters.web.httpx.Client")
    def test_docx_suffix(self, mock_client_cls) -> None:
        """DOCX content type gets .docx suffix."""
        mock_client = _wire_httpx_client(mock_client_cls)

        mock_response = MagicMock()
        mock_response.iter_bytes.return_value = [b"PK"]
        mock_response.raise_for_status = MagicMock()
        mock_client.stream.return_value.__enter__ = MagicMock(return_value=mock_response)
        mock_client.stream.return_value.__exit__ = MagicMock(return_value=False)

        ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        path = _stream_binary_to_temp("https://example.com/report.docx", ct)

        try:
            assert path.suffix == ".docx"
        finally:
            path.unlink(missing_ok=True)

    @patch("adapters.web.httpx.Client")
    def test_unknown_content_type_uses_bin_suffix(self, mock_client_cls) -> None:
        """Unknown content type falls back to .bin suffix."""
        mock_client = _wire_httpx_client(mock_client_cls)

        mock_response = MagicMock()
        mock_response.iter_bytes.return_value = [b"data"]
        mock_response.raise_for_status = MagicMock()
        mock_client.stream.return_value.__enter__ = MagicMock(return_value=mock_response)
        mock_client.stream.return_value.__exit__ = MagicMock(return_value=False)

        path = _stream_binary_to_temp("https://example.com/file", "application/weird-type")

        try:
            assert path.suffix == ".bin"
        finally:
            path.unlink(missing_ok=True)


# ============================================================================
# Pre-extracted content bypass (passe → tool layer)
# ============================================================================


class TestPreExtractedContentBypass:
    """Test that pre_extracted_content from passe skips trafilatura."""

    @patch('tools.fetch.web.extract_web_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_pre_extracted_content_used_directly(self, mock_fetch, mock_extract) -> None:
        """When pre_extracted_content is set, extractor is NOT called."""
        from tools.fetch import fetch_web

        mock_fetch.return_value = WebData(
            url="https://spa.example.com",
            html='<html><head><title>SPA Page</title></head><body><div id="root"></div></body></html>',
            final_url="https://spa.example.com",
            status_code=200,
            content_type="text/html",
            cookies_used=True,
            render_method="passe",
            pre_extracted_content="# SPA Page\n\nRendered article content from passe.",
        )

        result = fetch_web("https://spa.example.com")

        assert result.type == "web"
        assert result.metadata["render_method"] == "passe"
        assert result.metadata["title"] == "SPA Page"
        mock_extract.assert_not_called()

    @patch('tools.fetch.web.extract_web_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_no_pre_extracted_content_calls_extractor(self, mock_fetch, mock_extract) -> None:
        """When pre_extracted_content is None, extractor is called as normal."""
        from tools.fetch import fetch_web

        mock_fetch.return_value = WebData(
            url="https://example.com/article",
            html='<html><head><title>Article</title></head><body>Long article content ' + 'x' * 500 + '</body></html>',
            final_url="https://example.com/article",
            status_code=200,
            content_type="text/html",
            cookies_used=False,
            render_method="http",
        )
        mock_extract.return_value = "# Article\n\nExtracted content."

        result = fetch_web("https://example.com/article")

        assert result.type == "web"
        mock_extract.assert_called_once()


# ============================================================================
# extraction_failed cue
# ============================================================================


class TestExtractionFailedCue:
    """Test that extraction failure stub produces an extraction_failed cue."""

    @patch('tools.fetch.web.extract_web_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_stub_content_sets_cue(self, mock_fetch, mock_extract) -> None:
        """Content with failure stub → cues['extraction_failed'] == True."""
        from tools.fetch import fetch_web

        mock_fetch.return_value = WebData(
            url="https://empty.example.com",
            html='<html><body></body></html>',
            final_url="https://empty.example.com",
            status_code=200,
            content_type="text/html",
            cookies_used=False,
            render_method="http",
        )
        mock_extract.return_value = (
            "# Untitled\n\n"
            "*Content extraction failed for https://empty.example.com*\n\n"
            "The page may require JavaScript rendering."
        )

        result = fetch_web("https://empty.example.com")

        assert result.cues.get("extraction_failed") is True

    @patch('tools.fetch.web.extract_web_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_real_content_no_cue(self, mock_fetch, mock_extract) -> None:
        """Real content → 'extraction_failed' not in cues."""
        from tools.fetch import fetch_web

        mock_fetch.return_value = WebData(
            url="https://example.com/article",
            html='<html><head><title>Good Article</title></head><body>Content</body></html>',
            final_url="https://example.com/article",
            status_code=200,
            content_type="text/html",
            cookies_used=False,
            render_method="http",
        )
        mock_extract.return_value = "# Good Article\n\nReal article content here."

        result = fetch_web("https://example.com/article")

        assert "extraction_failed" not in result.cues


# ============================================================================
# Title extraction with pre-extracted content (passe forced path)
# ============================================================================


class TestTitleExtractionWithPasse:
    """Title extraction behaviour when passe provides pre_extracted_content."""

    @patch('tools.fetch.web.extract_web_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_forced_browser_extracts_title_from_h1(self, mock_fetch, mock_extract) -> None:
        """Forced browser path: html='' → title extracted from H1 in pre_extracted_content."""
        from tools.fetch import fetch_web

        mock_fetch.return_value = WebData(
            url="https://spa.example.com",
            html='',  # Forced browser path sets html=''
            final_url="https://spa.example.com",
            status_code=200,
            content_type="text/html",
            cookies_used=True,
            render_method="passe",
            pre_extracted_content="# Great SPA App\n\nContent here.",
        )

        result = fetch_web("https://spa.example.com")

        assert result.metadata["title"] == "Great SPA App"
        mock_extract.assert_not_called()

    @patch('tools.fetch.web.extract_web_content')
    @patch('tools.fetch.web.fetch_web_content')
    def test_auto_fallback_keeps_html_for_title(self, mock_fetch, mock_extract) -> None:
        """Auto JS-detection fallback: original HTML preserved → real title extracted."""
        from tools.fetch import fetch_web

        mock_fetch.return_value = WebData(
            url="https://spa.example.com",
            html='<html><head><title>Great SPA App</title></head><body><div id="root"></div></body></html>',
            final_url="https://spa.example.com",
            status_code=200,
            content_type="text/html",
            cookies_used=True,
            render_method="passe",
            pre_extracted_content="# Great SPA App\n\nContent here.",
        )

        result = fetch_web("https://spa.example.com")

        assert result.metadata["title"] == "Great SPA App"
        mock_extract.assert_not_called()


# ============================================================================
# Cross-layer stub consistency (extractor ↔ tool cue detection)
# ============================================================================


class TestExtractionFailedStubConsistency:
    """Ensure the tool layer's cue detection matches the extractor's actual stub."""

    def test_extractor_stub_matches_cue_detection(self) -> None:
        """The marker string in tools/fetch/web.py must appear in the extractor's stub."""
        from extractors.web import extract_web_content
        from models import WebData

        # Create WebData that will produce the failure stub (empty HTML)
        web_data = WebData(
            url="https://stub-test.example.com",
            html="<html><body></body></html>",
            final_url="https://stub-test.example.com",
            status_code=200,
            content_type="text/html",
            cookies_used=False,
            render_method="http",
        )

        content = extract_web_content(web_data)

        from extractors.web import EXTRACTION_FAILED_CUE
        assert EXTRACTION_FAILED_CUE in content, (
            f"Extractor stub no longer contains '{EXTRACTION_FAILED_CUE}'. "
            f"Both layers must use the shared constant. Got: {content!r}"
        )
