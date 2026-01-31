"""
Search tool implementation.

Unified search across Drive and Gmail.
Deposits results to file (filesystem-first pattern).
"""

from typing import Any

from adapters.drive import search_files
from adapters.gmail import search_threads
from adapters.activity import search_comment_activities, get_file_activities
from models import DriveSearchResult, GmailSearchResult, MiseError, SearchResult, CommentActivity
from validation import escape_drive_query, sanitize_gmail_query
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


def do_search(
    query: str,
    sources: list[str] | None = None,
    max_results: int = 20,
) -> SearchResult:
    """
    Search across Drive and Gmail.

    Deposits results to mise-fetch/ and returns path + summary.
    Follows filesystem-first pattern for token efficiency.

    Args:
        query: Search terms
        sources: List of sources to search (default: ['drive', 'gmail'])
        max_results: Maximum results per source

    Returns:
        SearchResult with path to deposited file and result counts
    """
    if sources is None:
        sources = ["drive", "gmail"]

    result = SearchResult(query=query, sources=sources)

    # Drive search
    if "drive" in sources:
        try:
            escaped_query = escape_drive_query(query)
            drive_query = f"fullText contains '{escaped_query}' and trashed = false"
            drive_results = search_files(drive_query, max_results=max_results)
            result.drive_results = [format_drive_result(r) for r in drive_results]
        except MiseError as e:
            result.errors.append(f"Drive search failed: {e.message}")
        except Exception as e:
            result.errors.append(f"Drive search failed: {str(e)}")

    # Gmail search
    if "gmail" in sources:
        try:
            sanitized_query = sanitize_gmail_query(query)
            gmail_results = search_threads(sanitized_query, max_results=max_results)
            result.gmail_results = [format_gmail_result(r) for r in gmail_results]
        except MiseError as e:
            result.errors.append(f"Gmail search failed: {e.message}")
        except Exception as e:
            result.errors.append(f"Gmail search failed: {str(e)}")

    # Deposit results to file (filesystem-first pattern)
    path = write_search_results(query, result.full_results())
    result.path = str(path)

    return result


def format_activity_result(activity: CommentActivity) -> dict[str, Any]:
    """Convert CommentActivity to JSON-serializable dict."""
    return {
        "activity_id": activity.activity_id,
        "timestamp": activity.timestamp,
        "actor": {
            "name": activity.actor.name,
            "email": activity.actor.email,
        },
        "target": {
            "file_id": activity.target.file_id,
            "file_name": activity.target.file_name,
            "mime_type": activity.target.mime_type,
            "web_link": activity.target.web_link,
        },
        "action_type": activity.action_type,
        "mentioned_users": activity.mentioned_users,
    }


def do_search_activity(
    filter_type: str = "comments",
    file_id: str | None = None,
    max_results: int = 50,
) -> dict[str, Any]:
    """
    Search recent activity across Drive.

    Useful for finding:
    - Action items (comments mentioning you)
    - Recent discussions on your files
    - Activity history for a specific file

    Args:
        filter_type: Type of activity to find:
            - "comments": Comment-related activities (default)
            - "edits": Edit-related activities
            - "all": All activities
        file_id: If provided, get activity for this specific file only
        max_results: Maximum activities to return

    Returns:
        Dict with:
        - activities: List of activity objects
        - next_page_token: For pagination (if more results exist)
        - warnings: Any issues encountered
        - error: True if operation failed
    """
    try:
        if file_id:
            # Get activity for specific file
            api_filter = filter_type if filter_type in ("comments", "edits") else None
            result = get_file_activities(file_id, page_size=max_results, filter_type=api_filter)
        else:
            # Search all comment activities
            if filter_type != "comments":
                # Only comment search is supported for cross-file queries
                return {
                    "error": True,
                    "kind": "invalid_input",
                    "message": "Cross-file search only supports filter_type='comments'. Use file_id for other activity types.",
                }
            result = search_comment_activities(page_size=max_results)

        return {
            "activities": [format_activity_result(a) for a in result.activities],
            "activity_count": len(result.activities),
            "next_page_token": result.next_page_token,
            "warnings": result.warnings if result.warnings else None,
        }

    except MiseError as e:
        return {
            "error": True,
            "kind": e.kind.value,
            "message": e.message,
        }
    except Exception as e:
        return {
            "error": True,
            "kind": "unknown",
            "message": str(e),
        }
