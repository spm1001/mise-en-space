"""
Calendar list + fan-out — the account's calendarList and a search across it
(mise-cegeva, 2026-09-14).

Sibling of adapters/calendar.py rather than more of it: that module sits at
the 500-line ratchet, and "which calendars can this account see" is the
cohesive slice that funds the new capability. Needs calendar.readonly —
see oauth_config.py for the re-consent story.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from adapters.calendar import _cap_events, _event_start_dt, list_events
from adapters.http_client import get_sync_client
from models import CalendarEvent, CalendarSearchResult, ErrorKind, MiseError
from retry import with_retry


# calendarList lives under users/me — the account's view of which calendars
# it can see, not a property of any one calendar
_CALENDAR_LIST_API = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
# Parallel per-calendar reads in list_all_events; a list rarely exceeds this
_FAN_OUT_WORKERS = 8


@with_retry(max_attempts=3, delay_ms=1000)
def list_calendars() -> list[dict[str, Any]]:
    """The calendars this account can read events from — its calendarList
    (mise-cegeva). 'primary' is one entry among several: a partner's or a
    team's calendar shared into the account shows in the UI beside your own,
    and a read of primary alone misses it with no sign.

    Needs calendar.readonly (added 2026-09-14). calendar.events covers events
    on a calendar you can NAME and never this listing, so a token minted
    before that date 403s here while every events read still works; callers
    fall back to 'primary' and teach the re-consent on that 403.

    Returns [{id, summary, primary}] with the primary first. minAccessRole=
    reader leaves out free/busy-only shares, which 404 on events.list.
    """
    client = get_sync_client()
    params: dict[str, Any] = {
        "minAccessRole": "reader",
        "fields": "items(id,summary,summaryOverride,primary),nextPageToken",
    }
    calendars: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        if page_token:
            params["pageToken"] = page_token
        response = client.get_json(_CALENDAR_LIST_API, params=params)
        for item in response.get("items", []):
            calendars.append({
                "id": item["id"],
                # summaryOverride is the user's own rename of a shared calendar
                "summary": item.get("summaryOverride") or item.get("summary") or item["id"],
                "primary": bool(item.get("primary")),
            })
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    calendars.sort(key=lambda c: not c["primary"])  # stable: primary first
    return calendars


def list_all_events(
    days_back: int = 7,
    days_forward: int = 7,
    max_results: int = 50,
    query: str = "",
    time_min: datetime | None = None,
    time_max: datetime | None = None,
) -> CalendarSearchResult:
    """Events across every calendar in the account's list, merged (mise-cegeva).

    Fans list_events out over list_calendars() in parallel, merges, drops
    duplicate event ids (an invitation you also hold on a shared calendar is
    the same Google event id — the primary's copy wins), and applies the same
    overflow selection as a single-calendar read. The result's .calendars
    names every calendar read, so a null reads as "none on these" and not
    "none anywhere".

    Two degraded paths, both disclosed rather than raised: calendarList
    refused (a token predating calendar.readonly) falls back to 'primary'
    and sets .calendar_list_error for the tools layer to turn into the
    re-consent cue; one calendar failing to read becomes a .calendars_failed
    record plus a .warnings line while the rest still answer — so coverage is
    .calendars + .calendars_failed, never .calendars alone (mise-gudeci). Read
    what you can, say what you could not.
    """
    now = datetime.now(timezone.utc)
    explicit_window = time_min is not None or time_max is not None
    warnings: list[str] = []
    list_error: str | None = None
    try:
        calendars = list_calendars()
    except MiseError as e:
        if e.kind is not ErrorKind.PERMISSION_DENIED:
            raise
        list_error = e.message
        calendars = [{"id": "primary", "summary": "primary", "primary": True}]
    if not calendars:  # a list with nothing readable still has the owner's own
        calendars = [{"id": "primary", "summary": "primary", "primary": True}]

    def _one(cal: dict[str, Any]) -> CalendarSearchResult | MiseError:
        try:
            return list_events(
                days_back=days_back, days_forward=days_forward,
                max_results=max_results, query=query,
                time_min=time_min, time_max=time_max, calendar_id=cal["id"],
            )
        except MiseError as e:
            return e

    with ThreadPoolExecutor(max_workers=min(len(calendars), _FAN_OUT_WORKERS)) as pool:
        outcomes = list(pool.map(_one, calendars))

    events: list[CalendarEvent] = []
    seen: set[str] = set()
    truncated = False
    read: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for cal, outcome in zip(calendars, outcomes):
        if isinstance(outcome, MiseError):
            # Both channels, deliberately: .warnings is the prose a library caller
            # already reads; .calendars_failed is the record the tools layer needs
            # to count coverage honestly (mise-gudeci). A 404 (the id) and a 403
            # (their sharing) are both named, never swallowed.
            failed.append({
                "id": cal["id"], "summary": cal["summary"],
                "kind": outcome.kind.value, "error": outcome.message,
            })
            warnings.append(
                f"{cal['summary']} ({cal['id']}) could not be read: {outcome.message}"
            )
            continue
        read.append(cal)
        truncated = truncated or outcome.truncated
        for event in outcome.events:
            if event.event_id in seen:
                continue
            seen.add(event.event_id)
            events.append(event)
    events.sort(key=_event_start_dt)
    events, capped = _cap_events(events, max_results, explicit_window, now)
    return CalendarSearchResult(
        events=events,
        truncated=truncated or capped,
        warnings=warnings,
        calendars=read,
        calendars_failed=failed,
        calendar_list_error=list_error,
    )
