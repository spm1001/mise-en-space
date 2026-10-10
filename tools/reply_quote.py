"""
The quoted original beneath a reply draft (mise-wujuza).

Gmail's UI reply appends the message being answered as a gmail_quote block
after the signature. mise's replies carried nothing from February 2026 until
this module, so every bare reply cut its thread's history for whoever was
added later or read on a client that doesn't stitch conversations.

One rule shapes everything here: the quote is built from the anchor
message's ORIGINAL parts (EmailMessage.body_html / body_text as the adapter
parsed them), never from the fetch extractor's output. The extractor strips
each message's own quoted history so a reader sees every message once; quoting
that cleaned view would carry only the anchor's newest words and sever the
chain this exists to keep. Message N as sent already nests N-1, so quoting the
last message carries the whole chain — which is also why there is no
quote='full'.
"""

import re
from dataclasses import dataclass, field
from html import escape

from extractors.quoted_history import html_has_history, text_has_history
from models import EmailMessage

QUOTE_MODES = ("last", "none")

# Gmail's own reply markup (gmail_attr + blockquote.gmail_quote), styles verbatim.
_BLOCKQUOTE_STYLE = "margin:0px 0px 0px 0.8ex;border-left:1px solid rgb(204,204,204);padding-left:1ex"

# Every pattern here is linear on a stranger's input: a tag runs to the next
# '>' outside a quoted attribute value, and a quoted value is bounded. The
# first cut's .* and [^>]* took seconds on 40 KB of repeated '<img ' (essayeur,
# 2026-10-10), minutes at a megabyte, on a server thread.
_ATTRS = r"""(?:"[^"]{0,4096}"|'[^']{0,4096}'|[^'"<>])*"""
_DOC_TAG = re.compile(rf"</?(?:html|body|head|meta|link|base)\b{_ATTRS}>|<!DOCTYPE[^<>]*>", re.IGNORECASE)
_BLOCK_OPEN = re.compile(rf"<(style|script|head|title)\b{_ATTRS}>", re.IGNORECASE)
_IMG = re.compile(rf"<img\b{_ATTRS}>", re.IGNORECASE)
_BODY_OPEN = re.compile(rf"<body\b{_ATTRS}>", re.IGNORECASE)
_CID_SRC = re.compile(r"\bsrc\s*=\s*[\"']?cid:", re.IGNORECASE)
_ALT = re.compile(r"""\balt\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)


@dataclass
class Quote:
    """The quote block for each MIME part, plus what the caller should be told."""
    html: str = ""
    text: str = ""
    cues: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def validate_quote(quote: str | None) -> str | None:
    """None when acceptable, else the teaching message."""
    if quote is None or quote in QUOTE_MODES:
        return None
    return (f"quote={quote!r} is not a quote mode — use 'last' (default: the message "
            "being answered, which already nests the earlier ones) or 'none'.")


def attribution(msg: EmailMessage) -> str:
    """Gmail's en-GB attribution line, dated in the anchor's own Date offset."""
    d = msg.date
    when = f"On {d:%a}, {d.day} {d:%b %Y} at {d:%H:%M}, " if d else "On an undated message, "
    return f"{when}{msg.from_address} wrote:"


def _body_inner(html: str) -> str:
    """What sits between <body ...> and the last </body>, or the whole input."""
    opener = _BODY_OPEN.search(html)
    if not opener:
        return html
    end = html.lower().rfind("</body")
    return html[opener.end():end] if end >= opener.end() else html[opener.end():]


def _drop_blocks(html: str) -> str:
    """Remove <style>, <script>, <head> and <title> blocks: in a quote they would
    restyle or script the reply itself. An unclosed opener loses only its tag."""
    out, pos, low = [], 0, html.lower()
    unclosed: set[str] = set()  # once a tag has no close ahead, no later opener has one either
    for m in _BLOCK_OPEN.finditer(html):
        if m.start() < pos:
            continue
        out.append(html[pos:m.start()])
        tag = m.group(1).lower()
        close = -1 if tag in unclosed else low.find(f"</{tag}", m.end())
        if close < 0:
            unclosed.add(tag)
            pos = m.end()
        else:
            gt = html.find(">", close)
            pos = gt + 1 if gt >= 0 else len(html)
    out.append(html[pos:])
    return "".join(out)


def _quoted_html_body(msg: EmailMessage) -> tuple[str, int]:
    """The anchor's HTML as an embeddable fragment, and how many cid images were dropped."""
    if not msg.body_html:
        plain = escape(msg.body_text or "")
        return plain.replace("\n", "<br>\n"), 0
    html = _DOC_TAG.sub("", _drop_blocks(_body_inner(msg.body_html)))

    # cid: images point at parts of the ORIGINAL message, which a new draft
    # doesn't carry, so each renders as a broken box. Keep the alt text instead.
    dropped = 0

    def _alt(m: re.Match[str]) -> str:
        nonlocal dropped
        if not _CID_SRC.search(m.group(0)):
            return m.group(0)
        dropped += 1
        alt = _ALT.search(m.group(0))
        text = (alt.group(1) or alt.group(2)) if alt else ""
        return f"[image: {escape(text)}]" if text else "[image]"

    return _IMG.sub(_alt, html), dropped


def has_history(msg: EmailMessage) -> bool:
    """Does this message already quote what came before it?"""
    return html_has_history(msg.body_html) or text_has_history(msg.body_text)


def build_quote(anchor: EmailMessage, earlier_messages: int) -> Quote:
    """Quote the anchor beneath a reply, the way Gmail's UI reply does.

    earlier_messages: live messages in the thread before the anchor. When
    there are some and the anchor quotes none of them, the chain recipients
    see stops at the anchor — said in a warning, because the caller can
    fix it (mention or forward what matters) and nothing else will tell them.
    """
    line = attribution(anchor)
    sender_html = escape(line)
    body_html, dropped = _quoted_html_body(anchor)
    html = (
        '<br><div class="gmail_quote gmail_quote_container">'
        f'<div dir="ltr" class="gmail_attr">{sender_html}<br></div>'
        f'<blockquote class="gmail_quote" style="{_BLOCKQUOTE_STYLE}">{body_html}</blockquote></div>'
    )
    original = (anchor.body_text or "").rstrip("\n")
    quoted_lines = "\n".join(f"> {ln}" if ln else ">" for ln in original.split("\n"))
    text = f"\n\n{line}\n\n{quoted_lines}\n"

    q = Quote(html=html, text=text)
    q.cues["quoted"] = f"the message being answered, from {anchor.from_address}, is quoted beneath the signature"
    if dropped:
        q.warnings.append(
            f"{dropped} inline image(s) in the quoted message were replaced by their alt text "
            "(they live in the original message, not this draft).")
    if earlier_messages and not has_history(anchor):
        q.warnings.append(
            f"The message being answered quotes none of the {earlier_messages} earlier message(s) in "
            "this thread, so the chain recipients see stops there. If someone new is on this "
            "reply and needs the earlier context, say so in the reply or forward it.")
    return q
