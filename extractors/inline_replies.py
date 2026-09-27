"""Inline replies written inside a quoted message (mise-lopune).

A reply that says "response below" and answers inside the quote reaches the
plain-text part with its answers '>'-prefixed like the text they answer, so
quote stripping deletes them and the deposit reads as a complete, empty
reply. So the quote is compared with the thread's earlier messages instead:
runs of words inside the quote that no earlier message contains are the
replier's own.

Word 4-gram shingles rather than difflib: indifferent to rewrapping, to which
earlier message is quoted, and to nested quote levels, and linear in length.

Novel text in a quote is NOT enough on its own. The first sweep over real
mail (27 Sep 2026: 43 multi-message threads, 10 firings, 1 true) found that
quotes routinely carry text the earlier message as fetched never had — the
legal footer a mail gateway appends in transit, the links a client adds as it
quotes an address or phone number, the header block of a forward, a chain
of messages that never reached this thread. So the gates: the reply's own
text must point into the quote (or be empty); link targets, header lines and
footer paragraphs count as neither old nor new; and a passage must be an
INSERTION — the quoted words either side of it must sit side by side in an
earlier message, so that deleting the passage restores what was quoted. A
second sweep (97 threads that say "below" or "inline") kept 3 true
answer-in-quote replies and rejected every gateway footer and forward header.
"""

import re
from dataclasses import dataclass

_WORD = re.compile(r"\w+")
_QUOTE_PREFIX = re.compile(r"^(>\s?)+")
# Text a client or gateway adds around a quote, whose words count as known:
# link targets (<https://…>, <mailto:…>, <+44…>, <name@host>, bare URLs),
# message-header lines, and any paragraph carrying a legal-footer phrase.
_LINK = re.compile(r"<[^\s<>]+>|\b(?:https?://|www\.)\S+")
_HEADER_LINE = re.compile(
    r"^[*_\s]*(?:from|sent|to|cc|bcc|date|subject)[*_\s]*:.*$|^.*(?:forwarded message|original message).*$",
    re.IGNORECASE | re.MULTILINE,
)
_FOOTER = re.compile(
    r"registered (?:office|in england)|registration no|company (?:no|number)|intended (?:solely|only) for"
    r"|received this (?:e-?mail|message) in error|(?:confidential|privileged) (?:information|and)",
    re.IGNORECASE,
)
# The reply's own words saying its content is inside the quote.
_POINTS_IN = re.compile(
    r"\b(?:below|beneath|underneath|inline|in-line|interspersed|in\s+(?:red|blue|green|"
    r"orange|purple|pink|yellow|bold|italics?|caps|colou?r))\b"
    r"|\b(?:my|see)\s+(?:comments|answers|responses|replies|notes)\b",
    re.IGNORECASE,
)
_SHINGLE = 4        # words per shingle
_MIN_RUN = 4        # a shorter novel run is reflow or rendering noise
_MERGE_GAP = 3      # known words allowed inside one reply before it splits
_MIN_COVERAGE = 0.3  # below this the quote is not of an earlier message
_CONTEXT_WORDS = 8  # quoted words shown before each reply


@dataclass
class InlineReply:
    after: str  # the quoted words the reply follows
    text: str


def reply_points_into_quote(own_text: str) -> bool:
    """Whether a reply's own text sends the reader into its quote, or is empty."""
    return bool(_POINTS_IN.search(own_text)) or len(_WORD.findall(own_text)) < 3


def quoted_text(body: str) -> str:
    """The '>'-prefixed lines of a body, unquoted at every nesting level."""
    lines = (line.lstrip() for line in body.split("\n"))
    return "\n".join(_QUOTE_PREFIX.sub("", line) for line in lines if line.startswith(">"))


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = [m.group().lower() for m in _WORD.finditer(text)]
    return {tuple(words[i:i + _SHINGLE]) for i in range(len(words) - _SHINGLE + 1)}


def _span(text: str, start: int, end: int) -> str:
    """Text between two offsets, carrying trailing punctuation, whitespace collapsed."""
    tail = re.match(r"\S*", text[end:])
    return " ".join(text[start:end + (tail.end() if tail else 0)].split())


def _added_spans(quote: str) -> list[tuple[int, int]]:
    """Offsets of text the quoting client or a gateway added, not the author."""
    spans = [(m.start(), m.end()) for m in _LINK.finditer(quote)]
    spans += [(m.start(), m.end()) for m in _HEADER_LINE.finditer(quote)]
    offset = 0
    for para in re.split(r"(\n\s*\n)", quote):
        if _FOOTER.search(para):
            spans.append((offset, offset + len(para)))
        offset += len(para)
    return spans


def _insertion_end(words: list[str], matched: list[bool], neutral: list[bool],
                   start: int, end: int, known: set[tuple[str, ...]]) -> int | None:
    """Where the passage from `start` ends, if deleting it restores the original.

    The two quoted words before the passage and the two after it must sit side
    by side in an earlier message: an answer typed between two lines of a
    question passes; a footer, a forward header or a chain of messages from
    outside the thread does not. The end is searched backwards from `end`
    because a short quoted tail ("Thanks, Priya") is too short to match a
    shingle on its own and so arrives glued to the passage.
    """
    before = [words[i] for i in range(start - 1, -1, -1) if matched[i]][:2][::-1]
    if len(before) < 2:
        return None
    for k in range(end, start + _MIN_RUN - 2, -1):
        after = [words[i] for i in range(k + 1, len(words)) if not neutral[i]][:2]
        if len(after) == 2 and tuple(before + after) in known:
            return k
    return None


def find_inline_replies(body: str, earlier: list[str]) -> tuple[list[InlineReply], float]:
    """Novel passages inside a message's quote, and the share of it that is known.

    Returns ([], coverage) when the quote is mostly unknown — a quote of a
    message outside the thread, which is not an inline reply.
    """
    quote = quoted_text(body)
    tokens = list(_WORD.finditer(quote))
    if not earlier or len(tokens) < _SHINGLE:
        return [], 0.0

    known: set[tuple[str, ...]] = set()
    for text in earlier:
        known |= _shingles(text)
    words = [t.group().lower() for t in tokens]
    added = _added_spans(quote)
    neutral = [any(a <= t.start() < b for a, b in added) for t in tokens]
    matched = [False] * len(words)
    for i in range(len(words) - _SHINGLE + 1):
        if tuple(words[i:i + _SHINGLE]) in known:
            matched[i:i + _SHINGLE] = [True] * _SHINGLE
    covered = [m or n for m, n in zip(matched, neutral)]
    coverage = sum(covered) / len(covered)
    if coverage < _MIN_COVERAGE:
        return [], coverage

    runs: list[list[int]] = []
    for i, is_known in enumerate(covered):
        if is_known:
            continue
        if runs and i - runs[-1][1] - 1 <= _MERGE_GAP:
            runs[-1][1] = i
        else:
            runs.append([i, i])

    replies = []
    for start, end in runs:
        stop = _insertion_end(words, matched, neutral, start, end, known)
        if stop is None or sum(not covered[i] for i in range(start, stop + 1)) < _MIN_RUN:
            continue
        text = _span(quote, tokens[start].start(), tokens[stop].end())
        ctx = max(0, start - _CONTEXT_WORDS)
        after = _span(quote, tokens[ctx].start(), tokens[start - 1].end()) if start else ""
        replies.append(InlineReply(after=after, text=text))
    return replies, coverage


def render_inline_replies(replies: list[InlineReply]) -> str:
    """The block appended to a reply's content — each passage after its anchor."""
    parts = [
        "**Inline replies** — text inside the quoted message that no earlier "
        "message in this thread contains, usually answers written into the "
        "quote. Each follows the quoted words shown:"
    ]
    for reply in replies:
        if reply.after:
            parts.append(f"> …{reply.after}")
        parts.append(reply.text)
    return "\n\n".join(parts)
