"""
HTML ↔ markdown conversion.

Two directions, two backends:
- HTML→markdown via markitdown (the `extraction` extra; falls back to tag
  stripping when absent). Needs a file path, so handles the tempfile dance.
  Used by adapters/gmail.py to pre-convert HTML email bodies before the pure
  extractor layer.
- markdown→HTML via python-markdown (core dep). Used by tools/draft.py to
  render email draft bodies so GFM tables and bold survive into Gmail.

Lives outside extractors/ because the HTML→markdown side does filesystem I/O.
"""

import os
import re
import tempfile
from html.parser import HTMLParser

import markdown


def markdown_to_html(content: str) -> str:
    """
    Render markdown to HTML for an email body. Pure, no I/O.

    GFM tables and **bold** must survive into the Gmail draft — the old
    <p>/<br>-only path emitted literal '|---|' rows and asterisks (field
    report mise-zolowa). python-markdown with the tables extension fixes it.

    Extensions: `tables` (GFM pipe tables), `nl2br` (single newline → <br>,
    so email line breaks behave as authors expect — plain markdown would
    collapse them), `sane_lists` (predictable list nesting). output_format
    'html' emits <br> not <br />, matching the prior contract.

    NOTE — raw HTML in `content` passes through unescaped (python-markdown's
    default). This is deliberate and safe HERE: the content is agent-composed
    markdown, and a draft is reviewed by the user before sending — this is not
    an untrusted-input boundary. Do NOT add output escaping/sanitising to
    "harden" it: that re-breaks table and bold rendering (the bug this fixes).
    Bare ampersands are still entity-escaped (& → &amp;).
    """
    return markdown.markdown(
        content,
        extensions=["tables", "nl2br", "sane_lists"],
        output_format="html",
    )


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

    # Completely hidden elements (display:none) — use BeautifulSoup for correct
    # nesting (regex can't handle nested tags like <div style="display:none"><div>x</div></div>)
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for el in soup.find_all(style=re.compile(r'display:\s*none', re.IGNORECASE)):
            el.decompose()
        html = str(soup)
    except ImportError:
        # Fallback: strip only self-closing/void hidden elements (safe subset)
        html = re.sub(
            r'<[^>]+style="[^"]*display:\s*none[^"]*"[^>]*/?>',
            '',
            html,
            flags=re.IGNORECASE
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


class _TextWithLinksParser(HTMLParser):
    """
    HTML → plain text, rendering <a href> as 'text (url)'.

    Line model: a newline on block-tag CLOSE only (plus <br>), so adjacent
    one-line-per-div Gmail markup reads as single line breaks while a
    deliberate <div><br></div> blank line survives as a paragraph gap.
    """

    _BLOCK_TAGS = {"p", "div", "tr", "li", "table", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []
        elif tag == "br":
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            text = "".join(self._link_text).strip()
            href = (self._href or "").strip()
            # Suppress the (url) suffix when it adds nothing: bare-URL link
            # text, or a mailto: wrapping the address it displays.
            redundant = href in (text, f"mailto:{text}")
            if href and text and not redundant:
                self.parts.append(f"{text} ({href})")
            else:
                self.parts.append(text or href)
            self._href = None
            self._link_text = []
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._link_text.append(data)
        else:
            self.parts.append(data)


def html_to_text_with_links(html: str) -> str:
    """
    Convert HTML to plain text, preserving hyperlinks as 'text (url)'.

    Pure, no I/O, stdlib-only — deliberately NOT markitdown, because this
    feeds the text/plain part of email drafts (a remote-safe op that must
    work in the slim build, where markitdown is absent). Block-level tags
    become newlines; entities are unescaped; whitespace is collapsed.

    Used to render the Gmail signature into a draft's plain-text part so
    links survive both MIME alternatives.
    """
    if not html or not html.strip():
        return ""
    parser = _TextWithLinksParser()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_html_tags(html: str) -> str:
    """
    Strip HTML tags and collapse whitespace. Pure, no I/O.

    This is the fallback when markitdown isn't available or fails.
    Also used directly by extractors that need a pure HTML-to-text path.
    """
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()
