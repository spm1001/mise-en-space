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


def clean_html_for_conversion(html: str) -> str:
    """
    Strip common email HTML cruft before markdown conversion.

    Email HTML is notoriously messy — this pre-filter removes patterns
    that cause artifacts in markdown conversion: tracking pixels, MSO
    conditionals, hidden elements, spacer cells, empty paragraphs.

    Pure function (no I/O). Called before convert_html_to_markdown.
    """
    if not html:
        return html

    # Hidden line breaks (Adobe's anti-tracking trick: 7.<br style="display:none"/>1.<br/>26)
    html = re.sub(
        r'<br\s+style="[^"]*display:\s*none[^"]*"\s*/?>',
        '',
        html,
        flags=re.IGNORECASE
    )

    # MSO conditionals (Outlook-specific blocks)
    html = re.sub(
        r'<!--\[if\s+.*?\]>.*?<!\[endif\]-->',
        '',
        html,
        flags=re.DOTALL | re.IGNORECASE
    )

    # Tracking pixels (1x1 images)
    html = re.sub(
        r'<img[^>]*(?:width|height)=["\']1["\'][^>]*/?>',
        '',
        html,
        flags=re.IGNORECASE
    )

    # Completely hidden elements (display:none)
    html = re.sub(
        r'<[^>]+style="[^"]*display:\s*none[^"]*"[^>]*>.*?</[^>]+>',
        '',
        html,
        flags=re.DOTALL | re.IGNORECASE
    )

    # Spacer cells with just &nbsp;
    html = re.sub(
        r'<td[^>]*>\s*(&nbsp;|\s)*\s*</td>',
        '',
        html,
        flags=re.IGNORECASE
    )

    # Empty paragraphs and divs (collapse whitespace)
    html = re.sub(
        r'<(p|div)[^>]*>\s*(&nbsp;|\s)*\s*</\1>',
        '',
        html,
        flags=re.IGNORECASE
    )

    return html


def strip_html_tags(html: str) -> str:
    """
    Strip HTML tags and collapse whitespace. Pure, no I/O.

    This is the fallback when markitdown isn't available or fails.
    Also used directly by extractors that need a pure HTML-to-text path.
    """
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()
