"""
Integration tests for Tasks API adapter.

Run with: uv run pytest tests/integration/test_tasks_adapter.py -v -m integration
"""

import pytest

from adapters.tasks import list_task_lists, list_tasks
from models import TaskList, TaskSearchResult, TaskItem


# ============================================================================
# list_task_lists
# ============================================================================


@pytest.mark.integration
def test_list_task_lists_returns_result() -> None:
    """list_task_lists returns a list of TaskList objects."""
    result = list_task_lists()

    assert isinstance(result, list)
    # Every Google account has at least one task list ("My Tasks")
    assert len(result) >= 1
    assert isinstance(result[0], TaskList)
    assert result[0].list_id  # Non-empty
    assert result[0].title  # Non-empty


# ============================================================================
# list_tasks
# ============================================================================


@pytest.mark.integration
def test_list_tasks_default_list() -> None:
    """list_tasks can query the default task list."""
    result = list_tasks(task_list_id="@default")

    assert isinstance(result, TaskSearchResult)
    assert isinstance(result.tasks, list)
    assert result.task_list_title == "My Tasks"


@pytest.mark.integration
def test_list_tasks_have_structure() -> None:
    """Each task has required fields populated."""
    result = list_tasks(task_list_id="@default", max_results=10)

    for task in result.tasks:
        assert isinstance(task, TaskItem)
        assert task.task_id  # Non-empty
        assert task.title  # Non-empty
        assert task.status in ("needsAction", "completed")


@pytest.mark.integration
def test_list_tasks_max_results() -> None:
    """max_results limits the number of tasks returned."""
    result = list_tasks(max_results=2)
    assert len(result.tasks) <= 2


@pytest.mark.integration
def test_list_tasks_with_completed() -> None:
    """show_completed=True includes completed tasks."""
    result = list_tasks(show_completed=True, max_results=50)

    assert isinstance(result, TaskSearchResult)
    # Can't guarantee completed tasks exist, just verify it doesn't error
