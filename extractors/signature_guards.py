"""
Guards that keep the trailing-contact-block rule off machine-generated mail.

talon_signature._strip_trailing_contact_block cuts everything below a short
"name block" when 3+ URLs follow it. That discriminator is inverted for SaaS
notification mail (Microsoft, Atlassian, GitHub, Google): there the trailing
URLs ARE the content — the call to action, the account link. A Microsoft
invitation lost its Accept-invitation link and 4 of its 5 real links to it,
silently, because 41% of the characters survived the ratio test (mise-kubolo,
2026-09-23). Split from talon_signature.py, which is size-ratcheted.
"""

import re

# A markdown link with anchor text, not an image: '[Accept invitation](https://...'.
# Only HTML-converted bodies carry these; a plain-text signature's links are bare.
_ANCHORED_LINK = re.compile(r'(?<!!)\[[^\]\n]*[A-Za-z][^\]\n]*\]\(https?://')
_LETTER = re.compile(r'[A-Za-z]')


def has_anchored_link(text: str) -> bool:
    """True when the text holds a call-to-action style link with anchor text."""
    return bool(_ANCHORED_LINK.search(text))


def is_texty(line: str) -> bool:
    """A name/title line has letters and is not markdown table furniture ('|  |')."""
    return bool(_LETTER.search(line)) and not line.startswith('|')
