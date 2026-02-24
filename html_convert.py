"""
HTML to markdown conversion via markitdown.

Markitdown requires a file path (no string API), so this module handles the
tempfile dance. Lives outside extractors/ because it does filesystem I/O.

Used by adapters/gmail.py to pre-convert HTML email bodies before they reach
the pure extractor layer.
"""

import os
import re
import tempfile


def convert_html_to_markdown(html: str) -> tuple[str, bool]:
    """
    Convert HTML to markdown using markitdown (local, fast).

    markitdown runs locally in ~100ms. Falls back to basic HTML tag
    stripping if markitdown fails or isn't available.

    Args:
        html: HTML content to convert

    Returns:
        Tuple of (markdown_string, used_fallback)
        used_fallback is True if markitdown failed and we stripped tags
    """
    if not html or not html.strip():
        return '', False

    try:
        from markitdown import MarkItDown

        # markitdown needs a file, so write to temp
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.html', delete=False, encoding='utf-8'
        ) as f:
            f.write(html)
            temp_path = f.name

        try:
            md = MarkItDown()
            result = md.convert(temp_path)
            markdown = result.text_content if result else ''

            if markdown:
                return markdown, False
            else:
                raise ValueError("markitdown returned empty result")

        finally:
            # Clean up temp file
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    except Exception:
        # Fallback: basic HTML tag stripping
        return strip_html_tags(html), True


def strip_html_tags(html: str) -> str:
    """
    Strip HTML tags and collapse whitespace. Pure, no I/O.

    This is the fallback when markitdown isn't available or fails.
    Also used directly by extractors that need a pure HTML-to-text path.
    """
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()
