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
    conditionals, hidden elements, empty paragraphs.

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

    # NOTE: empty <td>s are deliberately KEPT. A strip lived here until 2026-08
    # (mise-hisubi) and silently corrupted data tables: deleting an empty cell
    # shifts every later cell in the row left one column, so owners rendered
    # under DEADLINES. A surviving spacer cell costs only an empty markdown
    # column — cosmetic. Corruption beats cosmetics; don't reintroduce it.

    # Empty paragraphs and divs (collapse whitespace)
    html = re.sub(
        r'<(p|div)[^>]*>\s*(&nbsp;|\s)*\s*</\1>',
        '',
        html,
        flags=re.IGNORECASE
    )

    return html


class _TableGridScanner(HTMLParser):
    """Per-table structure for has_data_table: rows' text-cell counts + nesting."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict] = []   # finished tables
        self.stack: list[dict] = []    # open tables (sloppy mail HTML may never close them)
        self._cell_depth = 0
        self._cell_has_text = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            for t in self.stack:
                t["has_nested"] = True
            self.stack.append({"text_cells_per_row": [], "has_nested": False})
        elif tag == "tr" and self.stack:
            self.stack[-1]["text_cells_per_row"].append(0)
        elif tag in ("td", "th") and self.stack and self.stack[-1]["text_cells_per_row"]:
            self._cell_depth += 1
            self._cell_has_text = False

    def handle_data(self, data: str) -> None:
        if self._cell_depth and data.strip():
            self._cell_has_text = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell_depth:
            self._cell_depth -= 1
            if self._cell_has_text and self.stack and self.stack[-1]["text_cells_per_row"]:
                self.stack[-1]["text_cells_per_row"][-1] += 1
            self._cell_has_text = False
        elif tag == "table" and self.stack:
            self.tables.append(self.stack.pop())


def has_data_table(html_body: str | None) -> bool:
    """True when the HTML holds a plausible DATA table, not a layout wrapper.

    Two structural requirements, both needed (mise-voteki recalibration,
    2026-08-08): the table must contain NO nested table — data tables are
    flat, marketing/notification layouts are wrappers-in-wrappers — and at
    least two of its rows must each hold at least two cells carrying visible
    text. A first cut requiring only >=2 rows x >=2 cells swapped 31 of 40
    real promotions/updates messages (Slack pings, Docs shares, newsletters);
    with both requirements that corpus measured 0 false positives while the
    known-true Outlook data table still fired. A miss here is safe: the
    plain-text part is used, as it always was. Pure, no I/O.
    """
    if not html_body or '<table' not in html_body.lower():
        return False
    scanner = _TableGridScanner()
    try:
        scanner.feed(html_body)
    except Exception:
        return False  # unparseable HTML → no swap, plain part stands
    for t in scanner.tables + scanner.stack:
        if t["has_nested"]:
            continue
        if sum(1 for c in t["text_cells_per_row"] if c >= 2) >= 2:
            return True
    return False


def select_body_text(
    plain: str | None, html_body: str | None
) -> tuple[str | None, list[str]]:
    """Choose the body text for an email from its MIME alternatives.

    Plain text wins when both parts exist — it is the sender's own rendering,
    free of conversion noise — with one exception: a plain-text alternative
    cannot carry a table. Outlook (and most composers) flatten each row to
    bare lines of cell text, so row structure is destroyed before the message
    is ever sent (mise-voteki, live case thread 19fb9faca1565748). When the
    HTML part holds a data grid, the converted HTML is used instead and the
    swap is disclosed as a warning. The swap requires full markdown
    conversion — the slim build's tag-stripping fallback would lose the
    table too, so there the plain part stands.

    Returns (body_text, warnings). body_text is None when neither part exists.
    """
    if not html_body:
        return plain, []
    if not plain:
        cleaned = clean_html_for_conversion(html_body)
        converted, _ = convert_html_to_markdown(cleaned)
        return converted, []
    if has_data_table(html_body):
        cleaned = clean_html_for_conversion(html_body)
        converted, used_fallback = convert_html_to_markdown(cleaned)
        if converted.strip() and not used_fallback:
            return converted, [
                "Body taken from the HTML part: the plain-text alternative "
                "flattens the message's table(s) into bare lines of cell text."
            ]
    return plain, []


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
