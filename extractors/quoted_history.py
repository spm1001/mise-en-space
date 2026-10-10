"""
Does a message already carry the thread's history beneath it? (mise-wujuza)

Shared by the reply quoter (tools/reply_quote.py: the "chain stops here"
warning) and the draft reader (adapters/gmail_draft_attachments.py: whether an
update should keep a reply draft quoted), so both read quoted history the same
way — Gmail's markup, Apple Mail's cite blockquote, Outlook's reply header.
"""

import re

HTML_HISTORY = re.compile(
    r"gmail_quote|<blockquote|divRplyFwdMsg|appendonsend|OLK_SRC_BODY_SECTION"
    r"|-----\s*Original Message\s*-----", re.IGNORECASE)
TEXT_HISTORY = re.compile(
    r"^>|^On .+wrote:\s*$|^-----\s*Original Message\s*-----|^From: .+\n(?:Sent|Date): ",
    re.MULTILINE)


def html_has_history(html: str | None) -> bool:
    return bool(html and HTML_HISTORY.search(html))


def text_has_history(text: str | None) -> bool:
    return bool(text and TEXT_HISTORY.search(text))
