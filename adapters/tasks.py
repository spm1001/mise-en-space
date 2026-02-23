"""
Tasks adapter — Google Tasks API v1 wrapper.

Provides task listing for surfacing action items. Tasks can come from
Google Docs assigned actions, Chat, or direct creation.
"""

from typing import Any

from adapters.services import get_tasks_service
from models import (
    TaskItem,
    TaskList,
    TaskSearchResult,
)
from retry import with_retry


def _parse_task(data: dict[str, Any]) -> TaskItem:
    """Parse a task from Tasks API response."""
    return TaskItem(
        task_id=data.get("id", ""),
        title=data.get("title", ""),
        status=data.get("status", "needsAction"),
        due=data.get("due"),
        notes=data.get("notes"),
        updated=data.get("updated"),
        completed=data.get("completed"),
        parent_id=data.get("parent"),
        web_link=data.get("selfLink"),
    )


def _parse_task_list(data: dict[str, Any]) -> TaskList:
    """Parse a task list from Tasks API response."""
    return TaskList(
        list_id=data.get("id", ""),
        title=data.get("title", ""),
        updated=data.get("updated"),
    )


@with_retry(max_attempts=3, delay_ms=1000)
def list_task_lists() -> list[TaskList]:
    """
    List all task lists for the authenticated user.

    Returns:
        List of TaskList objects.
    """
    service = get_tasks_service()
    response = service.tasklists().list(maxResults=100).execute()
    return [_parse_task_list(item) for item in response.get("items", [])]


@with_retry(max_attempts=3, delay_ms=1000)
def list_tasks(
    task_list_id: str = "@default",
    show_completed: bool = False,
    max_results: int = 100,
    page_token: str | None = None,
) -> TaskSearchResult:
    """
    List tasks from a task list.

    Args:
        task_list_id: Task list ID. "@default" for the user's default list.
        show_completed: Whether to include completed tasks.
        max_results: Maximum tasks to return (max 100).
        page_token: Pagination token for next page.

    Returns:
        TaskSearchResult with tasks.
    """
    service = get_tasks_service()

    kwargs: dict[str, Any] = {
        "tasklist": task_list_id,
        "maxResults": min(max_results, 100),
        "showCompleted": show_completed,
        "showHidden": show_completed,  # Hidden = completed + cleared
    }
    if page_token:
        kwargs["pageToken"] = page_token

    response = service.tasks().list(**kwargs).execute()

    tasks = [_parse_task(item) for item in response.get("items", [])]
    warnings: list[str] = []

    # Get list title for context
    task_list_title = None
    if task_list_id == "@default":
        task_list_title = "My Tasks"
    else:
        # Could fetch the list name, but avoid extra API call
        task_list_title = task_list_id

    return TaskSearchResult(
        tasks=tasks,
        task_list_title=task_list_title,
        next_page_token=response.get("nextPageToken"),
        warnings=warnings,
    )
