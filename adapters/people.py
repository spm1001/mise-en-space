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

from typing import Any

import httpx

from adapters.http_client import get_sync_client
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
    return DirectoryPerson(
        email=data.get("primaryEmail", ""),
        full_name=name.get("fullName") or "",
        given_name=name.get("givenName"),
        family_name=name.get("familyName"),
        title=org.get("title"),
        department=org.get("department"),
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
        data = client.get_json(
            f"{_ADMIN_USERS_API}/{email}",
            params={**_DOMAIN_PUBLIC, "projection": "full"},
        )
    except httpx.HTTPStatusError as e:
        raise _convert_error(e, email)
    return _parse_person(data)


@with_retry(max_attempts=3)
def search_people(query: str, max_results: int = 10) -> PeopleSearchResults:
    """Search the domain directory.

    `query` goes straight to the Admin SDK's own search syntax:

      - bare words match NAME and EMAIL only ("Neil Charles", "rupert.coghlan")
      - `orgDepartment:MIT` scopes to a department, `email:rupert.coghlan*`
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
