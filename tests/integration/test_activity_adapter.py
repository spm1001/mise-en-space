"""
Integration tests for Activity API adapter.

Run with: uv run pytest tests/integration/test_activity_adapter.py -v
"""

import json
import pytest
from pathlib import Path

from adapters.activity import search_comment_activities, get_file_activities
from models import ActivitySearchResult, CommentActivity, ActivityActor, ActivityTarget


IDS_FILE = Path(__file__).parent.parent.parent / "fixtures" / "integration_ids.json"


@pytest.fixture
def integration_ids() -> dict[str, str]:
    """Load integration test IDs from config file."""
    if not IDS_FILE.exists():
        pytest.skip(
            f"Integration IDs not configured. Create {IDS_FILE} with test IDs"
        )
    with open(IDS_FILE) as f:
        return json.load(f)


# ============================================================================
# search_comment_activities
# ============================================================================


@pytest.mark.integration
def test_search_returns_result() -> None:
    """search_comment_activities returns an ActivitySearchResult."""
    result = search_comment_activities(page_size=5)

    assert isinstance(result, ActivitySearchResult)
    assert isinstance(result.activities, list)
    assert isinstance(result.warnings, list)


@pytest.mark.integration
def test_search_activities_have_structure() -> None:
    """Each activity has required fields populated."""
    result = search_comment_activities(page_size=10)

    for act in result.activities:
        assert isinstance(act, CommentActivity)
        assert act.activity_id  # Non-empty
        assert act.timestamp  # Non-empty
        assert isinstance(act.actor, ActivityActor)
        assert isinstance(act.target, ActivityTarget)
        assert act.target.file_id  # Non-empty
        assert act.action_type  # Non-empty


@pytest.mark.integration
def test_search_pagination() -> None:
    """Pagination token allows fetching next page."""
    first_page = search_comment_activities(page_size=2)

    if not first_page.next_page_token:
        pytest.skip("Not enough activities to test pagination")

    second_page = search_comment_activities(
        page_size=2, page_token=first_page.next_page_token
    )
    assert isinstance(second_page, ActivitySearchResult)

    # Pages should have different activities (if any on second page)
    if second_page.activities and first_page.activities:
        first_ids = {a.activity_id for a in first_page.activities}
        second_ids = {a.activity_id for a in second_page.activities}
        assert first_ids != second_ids, "Pagination returned same activities"


@pytest.mark.integration
def test_search_page_size_respected() -> None:
    """Requested page_size limits number of results."""
    result = search_comment_activities(page_size=3)
    assert len(result.activities) <= 3


# ============================================================================
# get_file_activities
# ============================================================================


@pytest.mark.integration
def test_file_activities_returns_result(integration_ids: dict[str, str]) -> None:
    """get_file_activities returns an ActivitySearchResult."""
    doc_id = integration_ids.get("test_doc_with_comments_id")
    if not doc_id:
        pytest.skip("test_doc_with_comments_id not in integration_ids.json")

    # Use no filter — comment filter may return empty on native Docs
    result = get_file_activities(doc_id, filter_type=None)

    assert isinstance(result, ActivitySearchResult)
    assert isinstance(result.activities, list)
    assert len(result.activities) > 0, "Expected activities on test doc"


@pytest.mark.integration
def test_file_activities_have_targets(integration_ids: dict[str, str]) -> None:
    """File activities should have targets referencing the requested file."""
    doc_id = integration_ids.get("test_doc_with_comments_id")
    if not doc_id:
        pytest.skip("test_doc_with_comments_id not in integration_ids.json")

    result = get_file_activities(doc_id, filter_type=None)

    for act in result.activities:
        assert act.target is not None
        assert act.target.file_id  # Non-empty


@pytest.mark.integration
def test_file_activities_comment_filter(integration_ids: dict[str, str]) -> None:
    """filter_type='comments' returns only comment activities."""
    doc_id = integration_ids.get("test_doc_with_comments_id")
    if not doc_id:
        pytest.skip("test_doc_with_comments_id not in integration_ids.json")

    result = get_file_activities(doc_id, filter_type="comments")

    comment_types = {"comment", "reply", "resolve", "reopen", "delete",
                     "suggest", "accept_suggestion", "reject_suggestion",
                     "assign", "unassign"}
    for act in result.activities:
        assert act.action_type in comment_types or act.action_type.startswith(
            ("post_", "assignment_", "suggestion_")
        ), f"Unexpected action type with comment filter: {act.action_type}"


@pytest.mark.integration
def test_file_activities_no_filter(integration_ids: dict[str, str]) -> None:
    """filter_type=None returns mixed activity types."""
    doc_id = integration_ids.get("test_doc_with_comments_id")
    if not doc_id:
        pytest.skip("test_doc_with_comments_id not in integration_ids.json")

    result = get_file_activities(doc_id, filter_type=None)

    types = {a.action_type for a in result.activities}
    # With no filter we expect more than just comment types
    # (edits, creates, etc.) — at least 2 distinct types
    assert len(types) >= 2, f"Expected mixed types, got: {types}"


@pytest.mark.integration
def test_file_activities_edit_filter(integration_ids: dict[str, str]) -> None:
    """filter_type='edits' returns non-comment activities."""
    doc_id = integration_ids.get("test_doc_with_comments_id")
    if not doc_id:
        pytest.skip("test_doc_with_comments_id not in integration_ids.json")

    result = get_file_activities(doc_id, filter_type="edits")

    # Activity API EDIT filter includes edits, renames, and other
    # non-comment modifications — verify none are comment types
    comment_types = {"comment", "reply", "resolve", "reopen",
                     "suggest", "accept_suggestion", "reject_suggestion",
                     "assign", "unassign"}
    for act in result.activities:
        assert act.action_type not in comment_types, (
            f"Edit filter returned comment activity: {act.action_type}"
        )


@pytest.mark.integration
def test_file_activities_invalid_file_id() -> None:
    """Invalid file ID raises MiseError (Activity API returns 500)."""
    from models import MiseError

    with pytest.raises(MiseError):
        get_file_activities("invalid-file-id-xyz")
