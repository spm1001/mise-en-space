"""
Search tool implementation.

Unified search across Drive, Gmail, and Activity.
Deposits results to file (filesystem-first pattern).
"""

from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from typing import Any

from adapters.drive import search_files
from adapters.gmail import search_threads
from adapters.activity import search_comment_activities
from models import (
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


def do_search(
    query: str,
    sources: list[str] | None = None,
    max_results: int = 20,
    base_path: Path | None = None,
    folder_id: str | None = None,
) -> SearchResult:
    """
    Search across Drive, Gmail, and Activity.

    Deposits results to mise/ and returns path + summary.
    Follows filesystem-first pattern for token efficiency.

    Args:
        query: Search terms (not used for activity source — activity returns
            recent comment events regardless of query).
        sources: List of sources to search (default: ['drive', 'gmail']).
            Valid sources: 'drive', 'gmail', 'activity'.
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

    # folder_id scopes to Drive only — Gmail and Activity have no folder concept
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

    # Run searches in parallel
    futures: dict[str, Future] = {}
    active_sources = []
    if search_drive:
        active_sources.append(("drive", _run_drive))
    if search_gmail:
        active_sources.append(("gmail", _run_gmail))
    if search_activity:
        active_sources.append(("activity", _run_activity))

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

    # Deposit results to file (filesystem-first pattern)
    path = write_search_results(query, result.full_results(), base_path=base_path)
    result.path = str(path)

    return result
