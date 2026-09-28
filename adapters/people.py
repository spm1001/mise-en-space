"""
People adapter — the Workspace staff directory, read as a plain user.

Answers "who is this colleague, what do they do, and who do they report to?"
using the Admin SDK Directory API's **domain_public** view.

WHY THIS IS NOT AN ADMIN CAPABILITY, despite the scope name
-----------------------------------------------------------
`admin.directory.user.readonly` reads alarming and the API is called the Admin
SDK, but Google documents `users.get` and `users.list` with
`viewType=domain_public` as available to ANY user on the domain — see
"Retrieve a user as a non-administrator" in the Directory API guide. Measured
against ITV's tenant 2026-08-10 (mise-mahiho): both calls return 200 on a
plain user token, while the SAME call WITHOUT `domain_public` returns
403 "Not Authorized to access this resource/api". That differing error is the
control — it proves `domain_public` is doing the work and the caller holds no
administrator rights.

**So `_DOMAIN_PUBLIC` must ride EVERY request in this module.** Drop it and the
call stops being a non-admin read; it does not degrade, it 403s. A unit test
pins that every call site sets it.

The other route considered and rejected: People API
`people:searchDirectoryPeople` (scope `directory.readonly`). It returns the
same core profile, but its "prefix query" matches names and email addresses
ONLY — measured, `'Data Scientist'` and `'Head of Strategy'` both return zero —
and it has no reverse lookup. This route is a strict superset.

Domain dependency: Workspace "Contact sharing" must be enabled, and individual
users may opt out of directory listing. An empty result is therefore not proof
a colleague does not exist.
"""

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from email.utils import getaddresses
from typing import Any
from urllib.parse import quote

import httpx

from adapters import directory_refusal
from adapters.http_client import get_sync_client
from adapters.org_map import division_for
from cues_util import current_user_email
from logging_config import logger
from models import DirectoryPerson, ErrorKind, MiseError, PeopleSearchResults
from retry import with_retry

_ADMIN_USERS_API = "https://admin.googleapis.com/admin/directory/v1/users"

# Load-bearing on every request — see the module docstring.
_DOMAIN_PUBLIC = {"viewType": "domain_public"}

# Admin SDK caps users.list at 500 per page; we never page here because a
# directory search that needs paging is the wrong shape of question.
_MAX_PAGE = 500



def _parse_person(data: dict[str, Any]) -> DirectoryPerson:
    """Parse one Admin SDK User resource into a DirectoryPerson."""
    name = data.get("name") or {}
    org = (data.get("organizations") or [{}])[0]
    manager = next(
        (
            r.get("value")
            for r in data.get("relations") or []
            if r.get("type") == "manager" and r.get("value")
        ),
        None,
    )
    phone = next(
        (p.get("value") for p in data.get("phones") or [] if p.get("value")), None
    )
    email = data.get("primaryEmail", "")
    department = org.get("department")
    return DirectoryPerson(
        email=email,
        full_name=name.get("fullName") or "",
        given_name=name.get("givenName"),
        family_name=name.get("familyName"),
        title=org.get("title"),
        department=department,
        division=division_for(email, department),
        organization=org.get("name"),
        location=org.get("location"),
        manager_email=manager,
        phone=phone,
        photo_url=data.get("thumbnailPhotoUrl"),
    )


def _convert_error(e: httpx.HTTPStatusError, subject: str) -> MiseError:
    """Map the Directory API's HTTP taxonomy onto MiseError kinds."""
    status = e.response.status_code
    body = e.response.text[:300]
    if status == 400 and "userKey" in body:
        # A GROUP address, not a person — users.get answers "Type not supported:
        # userKey". Expected rather than exceptional: team distribution lists
        # (e.g. team-list@example.com) sit in a large share of real threads, so this is a
        # routine outcome of placing senders and must read as one. Placing the
        # group itself would need the Groups API and its own scope; not worth a
        # re-consent until someone asks.
        return MiseError(
            ErrorKind.NOT_FOUND,
            f"{subject} is a group or alias, not an individual — no personal "
            "directory profile exists for it.",
            details={"subject": subject, "http_status": 400, "is_group": True},
        )
    if status == 404:
        return MiseError(
            ErrorKind.NOT_FOUND,
            f"No directory profile for {subject}. Either the address is wrong, "
            "the person has left, or they have opted out of the directory.",
            details={"subject": subject, "http_status": 404},
        )
    if status in (401, 403):
        # The commonest cause by far is a token minted before the directory
        # scope was added — teach the remedy rather than reporting a wall.
        return MiseError(
            ErrorKind.PERMISSION_DENIED,
            "Directory read refused. If this token predates directory support, "
            "re-authenticate with do(operation='setup_oauth', force=True) to pick "
            "up the admin.directory.user.readonly scope. If it persists, the "
            "domain's Contact sharing setting may be off.",
            details={"subject": subject, "http_status": status, "body": body},
        )
    if status == 429:
        return MiseError(
            ErrorKind.RATE_LIMITED,
            "Directory API quota exceeded",
            details={"subject": subject, "http_status": 429},
            retryable=True,
        )
    if status >= 500:
        return MiseError(
            ErrorKind.NETWORK_ERROR,
            f"Directory API server error: {status}",
            details={"subject": subject, "http_status": status},
            retryable=True,
        )
    return MiseError(
        ErrorKind.NETWORK_ERROR,
        f"Directory API HTTP {status}: {body}",
        details={"subject": subject, "http_status": status},
    )


@with_retry(max_attempts=3)
def get_person(email: str) -> DirectoryPerson:
    """Fetch one colleague's public domain profile by email address.

    Raises MiseError(NOT_FOUND) when the address has no directory profile.
    """
    client = get_sync_client()
    try:
        # Percent-encode: the address comes from mail headers, and a raw
        # '../groups/x@<domain>' walked onto another endpoint (a real scope 403
        # on a healthy token) while 'x/../<colleague>@<domain>' fetched that
        # colleague's profile (essayeur, 28 Sep — mise-hejeze).
        data = client.get_json(
            f"{_ADMIN_USERS_API}/{quote(email, safe='@')}",
            params={**_DOMAIN_PUBLIC, "projection": "full"},
        )
    except httpx.HTTPStatusError as e:
        raise _convert_error(e, email)
    return _parse_person(data)


@with_retry(max_attempts=3)
def search_people(query: str, max_results: int = 10) -> PeopleSearchResults:
    """Search the domain directory.

    `query` goes straight to the Admin SDK's own search syntax:

      - bare words match NAME and EMAIL only ("Jo Bloggs", "jo.bloggs")
      - `orgDepartment:MIT` scopes to a department, `email:jo.bloggs*`
        to an address prefix
      - a value containing a SPACE needs `=` and SINGLE quotes:
        `orgTitle='Head of Strategy'` works; `orgTitle:Head of Strategy` and
        `orgTitle:"Head of Strategy"` both return ZERO, with no error

    All measured 2026-08-10. Both zero-returning forms look exactly like a
    genuine absence, which is why do_search cues the working syntax whenever a
    plain query comes back empty rather than letting the caller read the zero
    as "nobody has that job".
    """
    client = get_sync_client()
    capped = max(1, min(max_results, _MAX_PAGE))
    try:
        data = client.get_json(
            _ADMIN_USERS_API,
            params={
                **_DOMAIN_PUBLIC,
                "customer": "my_customer",
                "query": query,
                "maxResults": str(capped),
                "projection": "full",
                "orderBy": "email",
            },
        )
    except httpx.HTTPStatusError as e:
        raise _convert_error(e, query)

    people = [_parse_person(u) for u in data.get("users") or []]
    return PeopleSearchResults(
        people=people,
        # A surviving page token is exact evidence that more matched — the same
        # fetched-vs-matched distinction Drive search draws (mise-werevi).
        truncated=bool(data.get("nextPageToken")),
    )


def get_direct_reports(email: str, max_results: int = 25) -> list[DirectoryPerson]:
    """Who reports TO this person. Best-effort: returns [] rather than raising.

    Used only to enrich a single-hit profile lookup, so a failure here must
    never fail the search that asked for it.
    """
    client = get_sync_client()
    try:
        data = client.get_json(
            _ADMIN_USERS_API,
            params={
                **_DOMAIN_PUBLIC,
                "customer": "my_customer",
                "query": f"manager={email}",
                "maxResults": str(max(1, min(max_results, _MAX_PAGE))),
                "projection": "full",
                "orderBy": "email",
            },
        )
    except Exception:
        return []
    return [_parse_person(u) for u in data.get("users") or []]


def expand_profile(person: DirectoryPerson) -> dict[str, Any]:
    """Resolve one person's reporting line into names, not just addresses.

    Gated by the caller on a SINGLE search hit — "who is X and who do they
    report to?" deserves a complete answer in one tool call, but paying two
    extra requests per result across a ten-person search would not. Same
    gating discipline as the checkbox oracle in adapters/docs.py.

    Best-effort throughout: every lookup here is decoration on a search that
    has already succeeded, so a failure returns less, never an error.
    """
    context: dict[str, Any] = {}

    if person.manager_email:
        try:
            mgr = get_person(person.manager_email)
            context["manager"] = mgr.to_dict()
        except Exception:
            # Keep the address we already have — losing the name is a smaller
            # loss than dropping the reporting line entirely.
            context["manager"] = {"email": person.manager_email}

    reports = get_direct_reports(person.email)
    if reports:
        context["direct_reports"] = [r.to_dict() for r in reports]

    return context


# --- Placing the people in a mailbox (mise-fajabe) -------------------------
#
# Sameer's ask: "if there's an email from Meghan Baddely, to know what she
# does and why she might be asking." That has to arrive at SEARCH time, not
# fetch time — triage is "which of these thirty do I read?", and placement is
# an input to that decision, so delivering it on fetch means paying the
# expensive call on every thread just to learn who sent it.
#
# What makes that affordable is not the API being fast, it is that inbox
# senders repeat relentlessly. A thirty-thread slice is ten to fifteen unique
# addresses, and across a session the same colleagues recur constantly — so
# one process-lifetime cache turns "a lookup per row" into "a lookup per
# colleague, once". Negative results are cached too: an external sender that
# doesn't resolve must not be re-queried on every subsequent search.

_PROFILE_CACHE: dict[str, dict[str, Any] | None] = {}
# Bounded so a long session sweeping a large mailbox cannot grow it without
# limit. Far above any realistic correspondent count, so it is a backstop
# rather than an eviction policy — hence the crude clear-all in _fetch_profile.
_CACHE_MAX = 2000
_ENRICH_WORKERS = 6


def clear_profile_cache() -> None:
    """Drop cached directory profiles and any standing refusal (test isolation, and re-auth)."""
    _PROFILE_CACHE.clear()
    directory_refusal.clear()


def address_of(header_value: str | None) -> str | None:
    """Bare lowercased address from a From/To-shaped header value."""
    if not header_value:
        return None
    parsed = getaddresses([header_value.strip().rstrip(",")])
    addr = parsed[0][1] if parsed and parsed[0][1] else None
    return addr.lower() if addr else None


def _own_domain() -> str | None:
    me = current_user_email()
    return me.rsplit("@", 1)[-1].lower() if me and "@" in me else None


def _fetch_profile(address: str) -> dict[str, Any] | None:
    """One cached lookup. None means 'no profile' — but only a real answer is
    cached (a profile, or NOT_FOUND). A refusal or a failed call is left
    uncached, so the address stays visibly unplaced and is asked again, never
    remembered as absent (mise-hejeze; a dropped connection did that too)."""
    if address in _PROFILE_CACHE:
        return _PROFILE_CACHE[address]
    if directory_refusal.blocking():
        return None
    value: dict[str, Any] | None = None
    try:
        value = get_person(address).to_dict()
    except MiseError as e:
        if e.kind is ErrorKind.PERMISSION_DENIED:  # the token, not the address
            directory_refusal.note(e)
            return None
        if e.kind is not ErrorKind.NOT_FOUND:  # NOT_FOUND: external, departed, opted out, a group
            logger.warning(f"Directory lookup for {address} failed ({e.kind.value}): {e.message}")
            directory_refusal.note_failure(f"{e.kind.value}: {e.message}")
            return None
    except Exception as e:
        # Best-effort by design: enrichment decorates a result that has already
        # succeeded, so an unforeseen failure must not fail it — but it says why.
        logger.warning(f"Directory lookup for {address} failed unexpectedly: {e!r}")
        directory_refusal.note_failure(repr(e))
        return None
    directory_refusal.clear()  # the directory answered, so it is readable
    if len(_PROFILE_CACHE) >= _CACHE_MAX:
        _PROFILE_CACHE.clear()  # the backstop: crude, and far above real use
    _PROFILE_CACHE[address] = value
    return value


def own_profile() -> dict[str, Any] | None:
    """The user's own directory profile, cached like any other lookup.

    Exists for relation arithmetic — a thread fetch names 'X is Y's manager'
    with the user in the set (mise-nelizu) — NEVER for attaching under a
    `people` key: placing the user to themselves is noise (see _wanted).
    One directory call per session, then the cache answers.
    """
    me = (current_user_email() or "").lower()
    if not me or "@" not in me:
        return None
    return _fetch_profile(me)


def _wanted(header_values: Iterable[str | None]) -> set[str]:
    """The own-domain addresses among these headers — never the user: a real
    inbox rendered an invite Sameer sent as "Sameer Modha — Client Strategy
    Data & Effectiveness Lead", and excluding here (not in the render) lets
    the fallback to the thread's ORIGINATOR place the colleague who started it."""
    domain = _own_domain()
    if not domain:
        return set()
    me = (current_user_email() or "").lower()
    return {
        addr
        for addr in (address_of(v) for v in header_values)
        if addr and addr.endswith(f"@{domain}") and addr != me
    }


def placement_gap(header_values: Iterable[str | None]) -> str | None:
    """The `people_unavailable` cue when own-domain addresses here are unplaced
    for want of an ANSWER — a refusal, or a failed call — rather than known to be
    absent (mise-hejeze). Only answers are cached, so a wanted address missing
    from the cache after profiles_for is exactly that. None for an all-external
    set, which had nothing to place."""
    unknown = sum(a not in _PROFILE_CACHE for a in _wanted(header_values))
    return directory_refusal.cue(unknown)


def profiles_for(header_values: Iterable[str | None]) -> dict[str, dict[str, Any]]:
    """Directory profiles for the own-domain addresses in these headers.

    Deduped, cached and fetched in parallel. Returns only what resolved, keyed
    by lowercased address. An absent key is an honest "not in the directory"
    only when no lookup failed or was refused — placement_gap says when that
    does not hold, and callers withhold the honest-absence wording then.
    """
    wanted = _wanted(header_values)
    if not wanted:
        return {}
    uncached = [a for a in wanted if a not in _PROFILE_CACHE]
    got = {a: _PROFILE_CACHE[a] for a in wanted if a in _PROFILE_CACHE}
    if uncached:  # one pass: a failed lookup is uncached, and must not re-fire
        with ThreadPoolExecutor(max_workers=min(_ENRICH_WORKERS, len(uncached))) as ex:
            got.update(zip(uncached, ex.map(_fetch_profile, uncached)))
    return {a: p for a, p in got.items() if p is not None}


SENDER_KEYS = ("from", "last_sender")  # the placed headers on a Gmail search row


def attach_profiles(rows: list[dict[str, Any]]) -> int:
    """Attach directory profiles to Gmail search rows, in place.

    Takes plain dicts rather than a SearchResult so this stays an adapter —
    it must not know the tools layer's types. Returns how many distinct
    people were placed, which is what the caller turns into a cue.

    Deliberately generous with what it ATTACHES and silent about what is
    SHOWN: the whole profile rides the deposit under `people`, and the render
    is one line built in models.py. Tuning how much a caller sees is then a
    render change, never a refetch — which matters because the right level of
    detail here is a question only looking at real output can answer.
    """
    if not rows:
        return 0
    people = profiles_for([r.get(k) for r in rows for k in SENDER_KEYS])
    if not people:
        return 0
    for row in rows:
        found = {}
        for key in SENDER_KEYS:
            addr = address_of(row.get(key))
            if addr and addr in people:
                found[addr] = people[addr]
        if found:
            row["people"] = found
            # Two placed people on one row is the "…and her boss" case. Pure
            # arithmetic over profiles already in hand — no extra call — so
            # there is no reason to withhold it.
            if len(found) > 1:
                pair = list(found.values())
                rel = relation_between(pair[0], pair[1])
                if rel:
                    row["people_relation"] = rel
    return len(people)


def relation_between(a: dict[str, Any], b: dict[str, Any]) -> str | None:
    """How two directory profiles relate — computed, never fetched.

    Once every participant carries a manager address, the reporting structure
    among them is arithmetic on data already in hand. This is the half of
    Sameer's ask about relating "an email from her boss" as exactly that.
    """
    a_mail, b_mail = a.get("email", "").lower(), b.get("email", "").lower()
    a_mgr = (a.get("manager") or "").lower()
    b_mgr = (b.get("manager") or "").lower()
    if a_mgr and a_mgr == b_mail:
        return f"{b.get('name')} is {a.get('name')}'s manager"
    if b_mgr and b_mgr == a_mail:
        return f"{a.get('name')} is {b.get('name')}'s manager"
    if a_mgr and a_mgr == b_mgr:
        return f"{a.get('name')} and {b.get('name')} report to the same manager"
    return None
