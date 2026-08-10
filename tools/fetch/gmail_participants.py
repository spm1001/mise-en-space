"""
Gmail participants extraction — who was on a thread, deduped and display-named,
and (mise-nelizu) placed from the staff directory.
"""

from email.utils import formataddr, getaddresses
from typing import Any

from adapters.people import address_of, own_profile, profiles_for
from cues_util import current_user_email


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


def relations_among(profiles: list[dict[str, Any]]) -> list[str]:
    """Reporting lines within a set of profiles — computed, never fetched.

    relation_between (adapters/people.py) covers the pair a search row can
    hold; a thread carries a SET, and pairwise output over a set is quadratic
    noise — six teammates would be fifteen 'report to the same manager'
    lines. So: a line per direct manager-report pair present in the set, and
    ONE grouped line per shared manager, only when that manager is not
    themselves in the set (when they are, the direct lines already say it).
    """
    by_email = {p.get("email", "").lower(): p for p in profiles if p.get("email")}
    lines: list[str] = []
    shared: dict[str, list[str]] = {}
    for p in profiles:
        mgr = (p.get("manager") or "").lower()
        if not mgr:
            continue
        boss = by_email.get(mgr)
        if boss is not None:
            lines.append(f"{boss.get('name')} is {p.get('name')}'s manager")
        else:
            shared.setdefault(mgr, []).append(p.get("name") or p.get("email", ""))
    for names in shared.values():
        if len(names) > 1:
            lines.append(
                ", ".join(names[:-1]) + f" and {names[-1]} report to the same manager"
            )
    return lines


def participants_with_placement(thread_data: Any) -> tuple[list[str], dict[str, Any]]:
    """Participants plus their directory placement (mise-nelizu).

    Search places senders because triage needs it (mise-fajabe); a fetch is
    the moment the thread is actually READ, and until this function a reader
    still got bare addresses there. Placement reuses the same adapter cache,
    so a thread found by search a moment ago re-pays nothing for its senders.

    Returns the display list unchanged, plus cue-ready extras the caller can
    merge without inspection:
      - `people`: full profiles keyed by address — own-domain directory hits
        only, across From+To+Cc+Bcc of every message
      - `people_relations`: reporting lines across the WHOLE set. The user's
        own cached profile joins the arithmetic (so a thread with their boss
        on it says so by name) but is never attached under `people`.
      - `people_note`: when some participants have no entry — external or
        directory-opted-out, an honest absence, not a failed lookup
    """
    participants = _extract_participants(thread_data)
    people = profiles_for(participants)
    if not people:
        return participants, {}
    extras: dict[str, Any] = {"people": people}

    pool = list(people.values())
    mine = own_profile()
    if mine:
        pool.append(mine)
    relations = relations_among(pool)
    if relations:
        extras["people_relations"] = relations

    me = (current_user_email() or "").lower()
    others = {a for a in (address_of(p) for p in participants) if a and a != me}
    if len(people) < len(others):
        extras["people_note"] = (
            f"{len(people)} of {len(others)} participants have directory "
            "profiles (under cues.people). An address with no entry is "
            "external or directory-opted-out — an honest absence, not a "
            "failed lookup."
        )
    return participants, extras
