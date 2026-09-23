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


_URL_HOST = re.compile(r'https?://(?:www\.)?([^/\s)>|\]]+)')


def link_loss_warnings(discarded: str) -> list[str]:
    """Name the links a signature strip threw away, whatever the character ratio.

    A ratio test cannot see this loss: the invitation kept 41% of its
    characters and 1 of 6 links (mise-kubolo). Real contact blocks will warn
    too; the hosts let the reader tell a LinkedIn footer from a call to action.
    """
    hosts = list(dict.fromkeys(_URL_HOST.findall(discarded)))
    if not hosts:
        return []
    return [
        f"Signature strip removed a trailing block holding {len(_URL_HOST.findall(discarded))} "
        f"link(s) ({', '.join(hosts[:5])}{', ...' if len(hosts) > 5 else ''}). If those were "
        "content, not a contact block, read the message in Gmail."
    ]
