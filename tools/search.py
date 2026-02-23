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
from adapters.gmail import search_threads
from adapters.activity import search_comment_activities
from adapters.calendar import list_events
from models import (
    CalendarEvent,
    CommentActivity,
    DriveSearchResult,
    GmailSearchResult,
    MiseError,
    SearchResult,
)
from validation import escape_drive_query, sanitize_gmail_query, validate_drive_id
from workspace.manager import write_search_results


def format_drive_result(result: DriveSearchResult) -> dict[str, Any]:
    """Convert DriveSearchResult to JSON-serializable dict."""
    output: dict[str, Any] = {
        "id": result.file_id,
        "name": result.name,
        "mimeType": result.mime_type,
        "modified": result.modified_time.isoformat() if result.modified_time else None,
        "url": result.web_view_link,
        "owners": result.owners,
        "snippet": result.snippet,
    }

    # Add email context for exfil'd files (cross-source linkage)
    if result.email_context:
        output["email_context"] = {
            "message_id": result.email_context.message_id,
            "from": result.email_context.from_address,
            "subject": result.email_context.subject,
            "hint": f"Use fetch('{result.email_context.message_id}') to get source email",
        }

    return output


def format_gmail_result(result: GmailSearchResult) -> dict[str, Any]:
    """Convert GmailSearchResult to JSON-serializable dict."""
    return {
        "thread_id": result.thread_id,
        "subject": result.subject,
        "snippet": result.snippet,
        "date": result.date.isoformat() if result.date else None,
        "from": result.from_address,
        "message_count": result.message_count,
        "has_attachments": result.has_attachments,
        "attachment_names": result.attachment_names,
    }


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
    query: str,
    sources: list[str] | None = None,
    max_results: int = 20,
    base_path: Path | None = None,
    folder_id: str | None = None,
) -> SearchResult:
    """
    Search across Drive, Gmail, Activity, and Calendar.

    Deposits results to mise/ and returns path + summary.
    Follows filesystem-first pattern for token efficiency.

    When both 'drive' and 'calendar' are in sources, Drive results are
    enriched with meeting context from matching calendar event attachments.

    Args:
        query: Search terms (not used for activity/calendar sources —
            activity returns recent comment events, calendar returns
            recent events with attachments).
        sources: List of sources to search (default: ['drive', 'gmail']).
            Valid sources: 'drive', 'gmail', 'activity', 'calendar'.
        max_results: Maximum results per source
        base_path: Base directory for deposits (defaults to cwd)
        folder_id: Optional Drive folder ID to scope results to immediate children only.
            Non-recursive — only files directly inside this folder are returned.
            Implies sources=['drive'] when provided.

    Returns:
        SearchResult with path to deposited file and result counts
    """
    if sources is None:
        sources = ["drive", "gmail"]

    # Validate folder_id before entering retry scope — ValueError here would
    # be swallowed into MiseError(UNKNOWN) by @with_retry in search_files()
    if folder_id is not None:
        validate_drive_id(folder_id, "folder_id")

    # folder_id scopes to Drive only — other sources have no folder concept
    excluded_sources: list[str] = []
    if folder_id is not None:
        excluded_sources = [s for s in sources if s != "drive"]
        sources = [s for s in sources if s == "drive"]

    result = SearchResult(query=query, sources=sources)

    # Scope notes — emitted unconditionally when folder_id is set
    if folder_id is not None:
        result.cues["scope"] = (
            f"non-recursive — results limited to immediate children of folder '{folder_id}'; "
            "files in subfolders are not included"
        )
        if excluded_sources:
            names = ", ".join(s.capitalize() for s in excluded_sources)
            result.cues["sources_note"] = f"{names} excluded — folder_id scopes to Drive only"

    search_drive = "drive" in sources
    search_gmail = "gmail" in sources
    search_activity = "activity" in sources
    search_calendar = "calendar" in sources

    def _run_drive() -> list[DriveSearchResult]:
        escaped_query = escape_drive_query(query)
        drive_query = f"fullText contains '{escaped_query}' and trashed = false"
        return search_files(drive_query, max_results=max_results, folder_id=folder_id)

    def _run_gmail() -> list[GmailSearchResult]:
        sanitized_query = sanitize_gmail_query(query)
        return search_threads(sanitized_query, max_results=max_results)

    def _run_activity() -> list[CommentActivity]:
        # Activity API doesn't support keyword search — returns recent comment events.
        # page_size maps to max_results for consistency.
        activity_result = search_comment_activities(page_size=max_results)
        return activity_result.activities

    def _run_calendar() -> list[CalendarEvent]:
        # Calendar returns recent events (±7 days by default).
        # max_results caps event count.
        calendar_result = list_events(max_results=max_results)
        return calendar_result.events

    # Run searches in parallel
    futures: dict[str, Future] = {}
    active_sources: list[tuple[str, Any]] = []
    if search_drive:
        active_sources.append(("drive", _run_drive))
    if search_gmail:
        active_sources.append(("gmail", _run_gmail))
    if search_activity:
        active_sources.append(("activity", _run_activity))
    if search_calendar:
        active_sources.append(("calendar", _run_calendar))

    if active_sources:
        with ThreadPoolExecutor(max_workers=len(active_sources)) as executor:
            for name, fn in active_sources:
                futures[name] = executor.submit(fn)

    # Collect results (errors are independent — one failing doesn't block the other)
    if "drive" in futures:
        try:
            result.drive_results = [format_drive_result(r) for r in futures["drive"].result()]
        except MiseError as e:
            result.errors.append(f"Drive search failed: {e.message}")
        except Exception as e:
            result.errors.append(f"Drive search failed: {str(e)}")

    if "gmail" in futures:
        try:
            result.gmail_results = [format_gmail_result(r) for r in futures["gmail"].result()]
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
            calendar_events = futures["calendar"].result()
            result.calendar_results = [format_calendar_result(e) for e in calendar_events]
        except MiseError as e:
            result.errors.append(f"Calendar search failed: {e.message}")
        except Exception as e:
            result.errors.append(f"Calendar search failed: {str(e)}")

    # Cross-reference: enrich Drive results with meeting context
    if result.drive_results and calendar_events:
        meeting_index = _build_meeting_context_index(calendar_events)
        if meeting_index:
            _enrich_drive_results_with_meetings(result.drive_results, meeting_index)

    # Deposit results to file (filesystem-first pattern)
    path = write_search_results(query, result.full_results(), base_path=base_path)
    result.path = str(path)

    return result
