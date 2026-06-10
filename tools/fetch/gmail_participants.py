"""
Gmail participants extraction — who was on a thread, deduped and display-named.
"""

from email.utils import formataddr, getaddresses
from typing import Any


def _extract_participants(thread_data: Any) -> list[str]:
    """Build participants list from a GmailThreadData (unique by canonical
    email, ordered by first appearance).

    Includes From + To + Cc + Bcc across every message. Reading From-only
    misses silent CC list members — a "Hi all" reply built from such data
    would lose the CCs entirely (see mise-vutato field report). Bcc shows
    up only on the user's own sent-folder copies (Gmail returns the Bcc
    header back to the sender, never to other recipients).

    Dedup is on the lowercased email part, not the raw header string —
    Gmail serialises the same person as '"a@x.com" <a@x.com>' on one
    message and 'Alice <a@x.com>' on another, so exact-string dedup
    over-counts (see mise-nucupi field report). The most informative
    display form wins: a real name beats a bare address, and a display
    name that merely repeats the address counts as bare.
    """
    # email-part key -> (address as first seen, best display name so far)
    entries: dict[str, tuple[str, str]] = {}
    order: list[str] = []

    def _add(raw: str) -> None:
        if not raw:
            return
        parsed = [(d, a) for d, a in getaddresses([raw]) if a]
        if not parsed:
            # No addr-spec found (e.g. "Undisclosed recipients:;") —
            # fall back to exact-string dedup on the raw value.
            if raw not in entries:
                entries[raw] = (raw, "")
                order.append(raw)
            return
        for display, addr in parsed:
            if display.strip().lower() == addr.lower():
                display = ""
            key = addr.lower()
            if key not in entries:
                entries[key] = (addr, display)
                order.append(key)
            elif len(display) > len(entries[key][1]):
                entries[key] = (entries[key][0], display)

    for msg in thread_data.messages:
        _add(msg.from_address)
        for addr in msg.to_addresses:
            _add(addr)
        for addr in msg.cc_addresses:
            _add(addr)
        for addr in msg.bcc_addresses:
            _add(addr)

    out: list[str] = []
    for key in order:
        addr, display = entries[key]
        out.append(formataddr((display, addr)) if display else addr)
    return out
