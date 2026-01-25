"""
Search tool implementation.

Unified search across Drive and Gmail.
"""

from typing import Any

from adapters.drive import search_files
from adapters.gmail import search_threads
from models import DriveSearchResult, GmailSearchResult, MiseError, SearchResult
from validation import escape_drive_query, sanitize_gmail_query


def format_drive_result(result: DriveSearchResult) -> dict[str, Any]:
    """Convert DriveSearchResult to JSON-serializable dict."""
    return {
        "id": result.file_id,
        "name": result.name,
        "mimeType": result.mime_type,
        "modified": result.modified_time.isoformat() if result.modified_time else None,
        "url": result.web_view_link,
        "owners": result.owners,
    }


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
    }


def do_search(
    query: str,
    sources: list[str] | None = None,
    max_results: int = 20,
) -> SearchResult:
    """
    Search across Drive and Gmail.

    Args:
        query: Search terms
        sources: List of sources to search (default: ['drive', 'gmail'])
        max_results: Maximum results per source

    Returns:
        SearchResult with separate result lists per source
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

    return result
