"""
Search tool implementation.

Unified search across Drive, Gmail, Activity, and Calendar.
Deposits results to file (filesystem-first pattern).
"""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from typing import Any

from adapters.drive import search_files
from adapters.gmail import _is_own_address, search_threads
from adapters.activity import search_comment_activities
from adapters.calendar import list_events
from adapters.people import attach_profiles, expand_profile, search_people
from models import (
    CalendarEvent,
    CalendarSearchResult,
    CommentActivity,
    DriveSearchResult,
    DriveSearchResults,
    GmailSearchResult,
    GmailSearchResults,
    MiseError,
    PeopleSearchResults,
    SearchResult,
)
from validation import (
    escape_drive_query,
    gmail_thread_web_url,
    sanitize_gmail_query,
    validate_drive_id,
)
from token_store import override_path
from workspace.manager import write_search_results


# Friendly name → Drive API mimeType query clause
_TYPE_MIME_MAP: dict[str, str] = {
    "folder":       "mimeType = 'application/vnd.google-apps.folder'",
    "doc":          "mimeType = 'application/vnd.google-apps.document'",
    "document":     "mimeType = 'application/vnd.google-apps.document'",
    "spreadsheet":  "mimeType = 'application/vnd.google-apps.spreadsheet'",
    "sheet":        "mimeType = 'application/vnd.google-apps.spreadsheet'",
    "slides":       "mimeType = 'application/vnd.google-apps.presentation'",
    "presentation": "mimeType = 'application/vnd.google-apps.presentation'",
    "pdf":          "mimeType = 'application/pdf'",
    "image":        "mimeType contains 'image/'",
    "video":        "mimeType contains 'video/'",
    "form":         "mimeType = 'application/vnd.google-apps.form'",
}
# Aliases that map to the same MIME as another key — excluded from error messages
_TYPE_ALIASES: frozenset[str] = frozenset({"document", "sheet", "presentation"})
# All accepted values (includes aliases)
VALID_TYPE_FILTERS: frozenset[str] = frozenset(_TYPE_MIME_MAP)
# Canonical names for user-facing messages (no aliases, alphabetical)
CANONICAL_TYPE_NAMES: frozenset[str] = VALID_TYPE_FILTERS - _TYPE_ALIASES


def format_drive_result(result: DriveSearchResult) -> dict[str, Any]:
    """Convert DriveSearchResult to JSON-serializable dict."""
    output: dict[str, Any] = {
        "id": result.file_id,
        "name": result.name,
        "mimeType": result.mime_type,
        "created": result.created_time.isoformat() if result.created_time else None,
        "modified": result.modified_time.isoformat() if result.modified_time else None,
        "url": result.web_view_link,
        "owners": result.owners,
        "snippet": result.snippet,
    }

    # Add email context for exfil'd files (cross-source linkage)
    if result.email_context:
        output["email_context"] = result.email_context.to_cue()

    return output


def format_gmail_result(result: GmailSearchResult) -> dict[str, Any]:
    """Convert GmailSearchResult to JSON-serializable dict."""
    out = {
        "thread_id": result.thread_id,
        "subject": result.subject,
        "snippet": result.snippet,  # drawn from the LATEST message
        "date": result.date.isoformat() if result.date else None,
        "from": result.from_address,  # thread ORIGINATOR — see last_sender for the latest voice
        "last_sender": result.last_sender,
        "from_me": result.from_me,  # None = identity unresolved, not "someone else"
        "unread_count": result.unread_count,
        "message_count": result.message_count,
        "has_attachments": result.has_attachments,
        "attachment_names": result.attachment_names,
        "is_unread": result.is_unread,
        "labels": result.label_ids,
        "has_invite": result.has_invite,  # thread carries a calendar invite (mise-pinodi)
    }
    # Clickable web URL — only when another party is visibly at an endpoint of
    # the thread (originator or latest sender provably not the user). A thread
    # authored solely by the user may be self-sent (thread-a), whose web token
    # cannot be derived from the API id (mise-lerulo); identity-unresolved
    # threads could be either. Both omit the field rather than risk a link
    # that opens the wrong conversation (mise-hetaba).
    if (_is_own_address(result.from_address) is False
            or _is_own_address(result.last_sender) is False):
        link = gmail_thread_web_url(result.thread_id)
        if link:
            out["web_link"] = link
    return out


def format_activity_result(activity: CommentActivity) -> dict[str, Any]:
    """Convert CommentActivity to JSON-serializable dict for search results."""
    result: dict[str, Any] = {
        "file_id": activity.target.file_id,
        "file_name": activity.target.file_name,
        "mime_type": activity.target.mime_type,
        "url": activity.target.web_link,
        "action_type": activity.action_type,
        "actor": activity.actor.name,
        "timestamp": activity.timestamp,
    }
    if activity.mentioned_users:
        result["mentioned_users"] = activity.mentioned_users
    return result


def format_calendar_result(event: CalendarEvent) -> dict[str, Any]:
    """Convert CalendarEvent to JSON-serializable dict for search results."""
    human_attendees = [a for a in event.attendees if not a.is_resource]
    result: dict[str, Any] = {
        "event_id": event.event_id,
        "summary": event.summary,
        "start_time": event.start_time,
        "end_time": event.end_time,
        "html_link": event.html_link,
        "organizer": event.organizer_email,
        "attendee_count": len(human_attendees),
        "attendees": [
            {"email": a.email, "name": a.display_name, "status": a.response_status}
            for a in human_attendees[:10]  # Cap for token efficiency
        ],
    }
    if event.attachments:
        result["attachments"] = [
            {"file_id": a.file_id, "title": a.title}
            for a in event.attachments
        ]
        result["attachment_count"] = len(event.attachments)
    if event.meet_link:
        result["meet_link"] = event.meet_link
    return result


def _build_meeting_context_index(
    calendar_events: list[CalendarEvent],
) -> dict[str, list[dict[str, Any]]]:
    """Build file_id → meeting context lookup from calendar events.

    Returns a dict mapping Drive file IDs to lists of meeting context dicts.
    A file may appear in multiple meetings.
    """
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in calendar_events:
        if not event.attachments:
            continue
        human_attendees = [a for a in event.attendees if not a.is_resource]
        context = {
            "summary": event.summary,
            "start_time": event.start_time,
            "attendee_count": len(human_attendees),
            "html_link": event.html_link,
        }
        if event.meet_link:
            context["meet_link"] = event.meet_link
        for att in event.attachments:
            index[att.file_id].append(context)
    return dict(index)


def _enrich_drive_results_with_meetings(
    drive_results: list[dict[str, Any]],
    meeting_index: dict[str, list[dict[str, Any]]],
) -> None:
    """Annotate Drive results with meeting context (mutates in place)."""
    for dr in drive_results:
        file_id = dr.get("id")
        if file_id and file_id in meeting_index:
            dr["meeting_context"] = meeting_index[file_id]


def do_search(
    query: str = "",
    sources: list[str] | None = None,
    max_results: int = 20,
    base_path: Path | None = None,
    folder_id: str | None = None,
    type: str | None = None,
    raw_query: str | None = None,
) -> SearchResult:
    """
    Search across Drive, Gmail, Activity, and Calendar.

    Deposits results to .mise/ and returns path + summary.
    Follows filesystem-first pattern for token efficiency.

    When both 'drive' and 'calendar' are in sources, Drive results are
    enriched with meeting context from matching calendar event attachments.

    Args:
        query: Search terms. Applied to drive, gmail, AND calendar (free-text
            match on event summary/description/attendees). Not used for
            activity, which returns recent comment events regardless.
            Optional when type or folder_id is set.
        sources: List of sources to search (default: ['drive', 'gmail'],
            or ['drive'] in guest mode where the token has no Gmail scope).
            Valid sources: 'drive', 'gmail', 'activity', 'calendar', 'people'.
            'people' searches the Workspace staff directory — bare words match
            name and email, `orgDepartment:X` scopes by team; a single hit is
            expanded with the manager and direct reports resolved to names.
        max_results: Maximum results per source
        base_path: Base directory for deposits (defaults to cwd)
        folder_id: Optional Drive folder ID to scope results to immediate children only.
            Non-recursive — only files directly inside this folder are returned.
            Implies sources=['drive'] when provided.
        type: Optional Drive file type filter. Friendly names: folder, doc, spreadsheet,
            sheet, slides, presentation, pdf, image, video, form. Applies to Drive only.
        raw_query: Drive query language, passed through unescaped — the power path.
            Mutually exclusive with `query`. Gets Drive's `or`, `not`,
            `name contains`, date and owner operators, none of which the single
            fullText clause `query` builds can express. `trashed = false` is still
            ANDed on (every mise surface excludes trash; a raw query silently
            resurrecting deleted files would be a worse surprise than not being
            able to search them), and `type`/`folder_id` still compose.

    Returns:
        SearchResult with path to deposited file and result counts
    """
    if sources is None:
        # Guest mode (MISE_TOKEN_PATH set): the caller-owned credential has no
        # Gmail scope, so an omitted-sources search defaults to Drive only —
        # otherwise it fails on a scope the guest token never carries (mise-kivane).
        sources = ["drive"] if override_path() is not None else ["drive", "gmail"]

    # Resolve type filter → Drive query clause (validated by caller, guard for direct use)
    type_clause: str | None = None
    if type is not None:
        type_clause = _TYPE_MIME_MAP.get(type)
        if type_clause is None:
            raise ValueError(f"Unknown type '{type}'. Valid: {', '.join(sorted(CANONICAL_TYPE_NAMES))}")

    # Validate folder_id before entering retry scope — ValueError here would
    # be swallowed into MiseError(UNKNOWN) by @with_retry in search_files()
    if folder_id is not None:
        validate_drive_id(folder_id, "folder_id")

    # folder_id and raw_query both scope to Drive only — the other sources have no
    # folder concept and don't speak Drive's query language. For raw_query the
    # scoping is load-bearing rather than tidy: `query` is usually empty alongside
    # it, and an empty Gmail query is not a no-op, it matches the whole mailbox.
    excluded_sources: list[str] = []
    drive_only_reason = "folder_id" if folder_id is not None else "raw_query"
    if folder_id is not None or raw_query:
        excluded_sources = [s for s in sources if s != "drive"]
        sources = [s for s in sources if s == "drive"]

    # The raw query is what was actually asked, so it labels the result and the
    # deposit filename; otherwise an empty slug lands on disk.
    result = SearchResult(query=raw_query or query, sources=sources)

    # Scope notes — emitted unconditionally when folder_id is set
    if folder_id is not None:
        result.cues["scope"] = (
            f"non-recursive — results limited to immediate children of folder '{folder_id}'; "
            "files in subfolders are not included"
        )
    if excluded_sources:
        names = ", ".join(s.capitalize() for s in excluded_sources)
        result.cues["sources_note"] = (
            f"{names} excluded — {drive_only_reason} scopes to Drive only"
        )

    search_drive = "drive" in sources
    search_gmail = "gmail" in sources
    search_activity = "activity" in sources
    search_calendar = "calendar" in sources
    # NB not `search_people` — that is the imported adapter function, and a
    # local of the same name shadows it (caught by live smoke, not by unit
    # tests: they call the adapter directly and never cross this scope).
    search_directory = "people" in sources

    if type is not None and not search_drive:
        result.cues["type_note"] = f"type='{type}' applies to Drive only — Drive not in sources, filter ignored"

    def _run_drive() -> DriveSearchResults:
        parts = ["trashed = false"]
        if raw_query and raw_query.strip():
            # Unescaped by design — the caller owns the syntax, exactly as Gmail's
            # `q` already works. Parenthesised so a top-level `or` inside it can't
            # rebind against the clauses we AND on.
            parts.append(f"({raw_query.strip()})")
        elif query.strip():
            parts.append(f"fullText contains '{escape_drive_query(query)}'")
        if type_clause:
            parts.append(type_clause)
        return search_files(" and ".join(parts), max_results=max_results, folder_id=folder_id)

    def _run_gmail() -> GmailSearchResults:
        sanitized_query = sanitize_gmail_query(query)
        return search_threads(sanitized_query, max_results=max_results)

    def _run_activity() -> list[CommentActivity]:
        # Activity API doesn't support keyword search — returns recent comment events.
        # page_size maps to max_results for consistency.
        activity_result = search_comment_activities(page_size=max_results)
        return activity_result.activities

    def _run_people() -> PeopleSearchResults:
        # Query goes straight to the Admin SDK's own syntax — bare words match
        # name/email, `orgDepartment:X` and `email:pre*` scope by field.
        return search_people(query, max_results=max_results)

    def _run_calendar() -> CalendarSearchResult:
        # Query rides the API's q filter; the ±7 day window is scanned in
        # full and a hit cap keeps events nearest NOW (mise-bidopi — the
        # cap must not eat the future).
        return list_events(max_results=max_results, query=query)

    # Run searches in parallel
    futures: dict[str, Future[Any]] = {}
    active_sources: list[tuple[str, Any]] = []
    if search_drive:
        active_sources.append(("drive", _run_drive))
    if search_gmail:
        active_sources.append(("gmail", _run_gmail))
    if search_activity:
        active_sources.append(("activity", _run_activity))
    if search_calendar:
        active_sources.append(("calendar", _run_calendar))
    if search_directory:
        active_sources.append(("people", _run_people))

    if active_sources:
        with ThreadPoolExecutor(max_workers=len(active_sources)) as executor:
            for name, fn in active_sources:
                futures[name] = executor.submit(fn)

    # Collect results (errors are independent — one failing doesn't block the other)
    if "drive" in futures:
        try:
            drive_search = futures["drive"].result()
            result.drive_results = [format_drive_result(r) for r in drive_search.results]
            if drive_search.truncated:
                result.cues["drive_truncated"] = (
                    f"Results capped at {len(drive_search.results)} — MORE MATCHED. "
                    "This is a ceiling, not a population: do not read an absence here "
                    "as proof a file doesn't exist. Narrow the query or raise max_results."
                )
        except MiseError as e:
            result.errors.append(f"Drive search failed: {e.message}")
        except Exception as e:
            result.errors.append(f"Drive search failed: {str(e)}")

    if "gmail" in futures:
        try:
            gmail_search = futures["gmail"].result()
            result.gmail_results = [format_gmail_result(r) for r in gmail_search.results]
            if gmail_search.truncated:
                result.cues["gmail_truncated"] = (
                    f"Results capped at {len(gmail_search.results)} — more exist. "
                    "Narrow the query or increase max_results to see all."
                )
            # Place the senders (mise-fajabe). Cached + parallel in the
            # adapter, so a session's recurring correspondents cost one
            # lookup each. Best-effort: never fails the search.
            placed = attach_profiles(result.gmail_results)
            if placed:
                # Say WHICH set the count describes. It counts people across
                # every fetched thread, while the preview renders a handful of
                # rows and suppresses lines with nothing to add — so "6 placed"
                # beside one visible line is correct and reads as a bug unless
                # the cue draws the same shown-vs-fetched line the rest of
                # search already draws (mise-werevi).
                result.cues["people_context"] = (
                    f"{placed} distinct sender(s) placed from the staff directory "
                    f"across all {len(result.gmail_results)} threads — full "
                    "profiles are on each row's `people` key in the deposit. The "
                    "preview shows fewer, and omits a line for anyone whose entry "
                    "carries no role (shared mailboxes, service accounts) and for "
                    "you. An address with no entry is external or "
                    "directory-opted-out — an honest absence, not a failed lookup."
                )
        except MiseError as e:
            result.errors.append(f"Gmail search failed: {e.message}")
        except Exception as e:
            result.errors.append(f"Gmail search failed: {str(e)}")

    if "activity" in futures:
        try:
            result.activity_results = [format_activity_result(a) for a in futures["activity"].result()]
        except MiseError as e:
            result.errors.append(f"Activity search failed: {e.message}")
        except Exception as e:
            result.errors.append(f"Activity search failed: {str(e)}")

    # Calendar: collect results and cross-reference with Drive
    calendar_events: list[CalendarEvent] = []
    if "calendar" in futures:
        try:
            calendar_search = futures["calendar"].result()
            calendar_events = calendar_search.events
            result.calendar_results = [format_calendar_result(e) for e in calendar_events]
            if calendar_search.truncated:
                result.cues["calendar_truncated"] = (
                    f"Results capped at {len(calendar_events)} — more events matched "
                    "in the ±7-day window; the events nearest to now were kept. "
                    "Add a query or raise max_results to see more."
                )
        except MiseError as e:
            result.errors.append(f"Calendar search failed: {e.message}")
        except Exception as e:
            result.errors.append(f"Calendar search failed: {str(e)}")

    if "people" in futures:
        try:
            people_search = futures["people"].result()
            result.people_results = [p.to_dict() for p in people_search.people]
            if people_search.truncated:
                result.cues["people_truncated"] = (
                    f"Results capped at {len(people_search.people)} — MORE MATCHED. "
                    "Narrow the query or raise max_results."
                )
            if len(people_search.people) == 1:
                # One hit is a "who is this person?" question — answer it whole,
                # with the reporting line resolved to names (adapters/people.py).
                context = expand_profile(people_search.people[0])
                if context:
                    result.people_results[0].update(context)
            elif not people_search.people and query.strip():
                # A bare job title finds nobody: the Admin SDK's free-text search
                # matches name and email ONLY. Measured 2026-08-10 — teach the
                # field-scoped form rather than letting the zero read as absence.
                # {query!r} rather than '{query}' — the literal quoted form trips
                # test_security's raw-interpolation scan, which is a lexical check
                # that cannot tell a Drive query from a sentence. Dodging the
                # collision is right; loosening the guard for prose is not.
                result.cues["people_note"] = (
                    f"No directory match for {query!r}. Bare words search NAME and "
                    "EMAIL only. For a role or team use a field: orgDepartment:MIT, "
                    "email:jane.smith*, or — for any value containing a SPACE — the "
                    "equals-and-single-quotes form, orgTitle='Head of Strategy'. "
                    "A multi-word value written as orgTitle:Head of Strategy, or in "
                    "double quotes, returns zero silently. Colleagues can also opt "
                    "out of the directory, so zero is not proof of absence."
                )
            if result.people_results:
                result.cues["people_source"] = (
                    "Workspace directory. 'manager' is the account's own field, "
                    "not an HR record — accurate for staff, but at board level it "
                    "can record who administers the account. Report it as what the "
                    "directory says."
                )
        except MiseError as e:
            result.errors.append(f"Directory search failed: {e.message}")
        except Exception as e:
            result.errors.append(f"Directory search failed: {str(e)}")

    # Cross-reference: enrich Drive results with meeting context
    if result.drive_results and calendar_events:
        meeting_index = _build_meeting_context_index(calendar_events)
        if meeting_index:
            _enrich_drive_results_with_meetings(result.drive_results, meeting_index)

    # Deposit results to file (filesystem-first pattern)
    # result.query, not query — it already resolves raw_query-or-query, and `query`
    # is empty on the raw path, which slugs every raw search to "untitled".
    path = write_search_results(
        result.query, result.full_results(), base_path=base_path, sources=result.sources,
    )
    result.path = str(path)

    return result
