"""
Tests for Tasks API adapter and models.
"""

from unittest.mock import patch, MagicMock

from tests.helpers import mock_api_chain

from models import (
    TaskItem,
    TaskList,
    TaskSearchResult,
)
from adapters.tasks import (
    _parse_task,
    _parse_task_list,
    list_task_lists,
    list_tasks,
)


# ============================================================================
# MODELS
# ============================================================================


class TestTaskModels:
    """Tests for Task data models."""

    def test_task_item_defaults(self) -> None:
        task = TaskItem(task_id="t1", title="Buy milk", status="needsAction")
        assert task.task_id == "t1"
        assert task.title == "Buy milk"
        assert task.status == "needsAction"
        assert task.due is None
        assert task.notes is None
        assert task.parent_id is None

    def test_task_item_full(self) -> None:
        task = TaskItem(
            task_id="t1",
            title="Review doc",
            status="completed",
            due="2026-02-25T00:00:00Z",
            notes="Check section 3",
            completed="2026-02-24T15:00:00Z",
            parent_id="t0",
        )
        assert task.status == "completed"
        assert task.due == "2026-02-25T00:00:00Z"
        assert task.parent_id == "t0"

    def test_task_list_defaults(self) -> None:
        tl = TaskList(list_id="l1", title="My Tasks")
        assert tl.list_id == "l1"
        assert tl.updated is None

    def test_search_result_defaults(self) -> None:
        result = TaskSearchResult(tasks=[])
        assert result.tasks == []
        assert result.task_list_title is None
        assert result.next_page_token is None
        assert result.warnings == []


# ============================================================================
# PURE PARSERS
# ============================================================================


class TestParseTask:
    """Test _parse_task with various structures."""

    def test_basic(self) -> None:
        task = _parse_task({
            "id": "t1",
            "title": "Follow up",
            "status": "needsAction",
            "updated": "2026-02-23T10:00:00Z",
        })
        assert task.task_id == "t1"
        assert task.title == "Follow up"
        assert task.status == "needsAction"
        assert task.updated == "2026-02-23T10:00:00Z"

    def test_completed_task(self) -> None:
        task = _parse_task({
            "id": "t2",
            "title": "Done thing",
            "status": "completed",
            "completed": "2026-02-22T12:00:00Z",
        })
        assert task.status == "completed"
        assert task.completed == "2026-02-22T12:00:00Z"

    def test_with_notes_and_due(self) -> None:
        task = _parse_task({
            "id": "t3",
            "title": "Review",
            "status": "needsAction",
            "due": "2026-02-28T00:00:00Z",
            "notes": "Look at section 5",
        })
        assert task.due == "2026-02-28T00:00:00Z"
        assert task.notes == "Look at section 5"

    def test_subtask(self) -> None:
        task = _parse_task({
            "id": "t4",
            "title": "Subtask",
            "status": "needsAction",
            "parent": "t1",
        })
        assert task.parent_id == "t1"

    def test_empty_data(self) -> None:
        task = _parse_task({})
        assert task.task_id == ""
        assert task.title == ""
        assert task.status == "needsAction"


class TestParseTaskList:
    """Test _parse_task_list with various structures."""

    def test_basic(self) -> None:
        tl = _parse_task_list({
            "id": "l1",
            "title": "My Tasks",
            "updated": "2026-02-23T10:00:00Z",
        })
        assert tl.list_id == "l1"
        assert tl.title == "My Tasks"
        assert tl.updated == "2026-02-23T10:00:00Z"

    def test_empty_data(self) -> None:
        tl = _parse_task_list({})
        assert tl.list_id == ""
        assert tl.title == ""


# ============================================================================
# list_task_lists (mocked service)
# ============================================================================


class TestListTaskLists:
    """Test list_task_lists with mocked Tasks API."""

    @patch("retry.time.sleep")
    @patch("adapters.tasks.get_tasks_service")
    def test_basic(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "tasklists.list.execute", {
            "items": [
                {"id": "l1", "title": "My Tasks"},
                {"id": "l2", "title": "Work"},
            ],
        })

        result = list_task_lists()

        assert len(result) == 2
        assert result[0].list_id == "l1"
        assert result[1].title == "Work"

    @patch("retry.time.sleep")
    @patch("adapters.tasks.get_tasks_service")
    def test_empty(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "tasklists.list.execute", {})

        result = list_task_lists()

        assert result == []


# ============================================================================
# list_tasks (mocked service)
# ============================================================================


def _api_task(
    *,
    task_id: str = "t1",
    title: str = "Test Task",
    status: str = "needsAction",
    due: str | None = None,
    notes: str | None = None,
) -> dict:
    """Build an API-shaped task dict."""
    task: dict = {
        "id": task_id,
        "title": title,
        "status": status,
    }
    if due:
        task["due"] = due
    if notes:
        task["notes"] = notes
    return task


class TestListTasks:
    """Test list_tasks with mocked Tasks API."""

    @patch("retry.time.sleep")
    @patch("adapters.tasks.get_tasks_service")
    def test_basic(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "tasks.list.execute", {
            "items": [_api_task()],
        })

        result = list_tasks()

        assert isinstance(result, TaskSearchResult)
        assert len(result.tasks) == 1
        assert result.tasks[0].title == "Test Task"
        assert result.task_list_title == "My Tasks"

    @patch("retry.time.sleep")
    @patch("adapters.tasks.get_tasks_service")
    def test_empty(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "tasks.list.execute", {})

        result = list_tasks()

        assert result.tasks == []

    @patch("retry.time.sleep")
    @patch("adapters.tasks.get_tasks_service")
    def test_pagination_token(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "tasks.list.execute", {
            "items": [_api_task()],
            "nextPageToken": "page2",
        })

        result = list_tasks()

        assert result.next_page_token == "page2"

    @patch("retry.time.sleep")
    @patch("adapters.tasks.get_tasks_service")
    def test_page_token_forwarded(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "tasks.list.execute", {"items": []})

        list_tasks(page_token="tok123")

        call_kwargs = mock_service.tasks().list.call_args[1]
        assert call_kwargs["pageToken"] == "tok123"

    @patch("retry.time.sleep")
    @patch("adapters.tasks.get_tasks_service")
    def test_max_results_capped(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "tasks.list.execute", {"items": []})

        list_tasks(max_results=500)

        call_kwargs = mock_service.tasks().list.call_args[1]
        assert call_kwargs["maxResults"] == 100

    @patch("retry.time.sleep")
    @patch("adapters.tasks.get_tasks_service")
    def test_show_completed(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "tasks.list.execute", {"items": []})

        list_tasks(show_completed=True)

        call_kwargs = mock_service.tasks().list.call_args[1]
        assert call_kwargs["showCompleted"] is True
        assert call_kwargs["showHidden"] is True

    @patch("retry.time.sleep")
    @patch("adapters.tasks.get_tasks_service")
    def test_custom_task_list(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "tasks.list.execute", {
            "items": [_api_task()],
        })

        result = list_tasks(task_list_id="custom-list-123")

        call_kwargs = mock_service.tasks().list.call_args[1]
        assert call_kwargs["tasklist"] == "custom-list-123"
        assert result.task_list_title == "custom-list-123"

    @patch("retry.time.sleep")
    @patch("adapters.tasks.get_tasks_service")
    def test_multiple_tasks(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_api_chain(mock_service, "tasks.list.execute", {
            "items": [
                _api_task(task_id="t1", title="First"),
                _api_task(task_id="t2", title="Second", status="completed"),
                _api_task(task_id="t3", title="Third", due="2026-03-01T00:00:00Z"),
            ],
        })

        result = list_tasks()

        assert len(result.tasks) == 3
        assert result.tasks[1].status == "completed"
        assert result.tasks[2].due == "2026-03-01T00:00:00Z"
