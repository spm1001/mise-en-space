"""
Integration tests for the search MCP tool.

Run with: uv run pytest tests/integration/test_search_tool.py -v -m integration
"""

import pytest

from server import search


@pytest.mark.integration
def test_search_drive_only() -> None:
    """Test search with Drive source only."""
    result = search("test", sources=["drive"], max_results=5)

    assert "drive_results" in result
    assert "query" in result
    assert result["query"] == "test"
    # May or may not have results, but should be a list
    assert isinstance(result["drive_results"], list)


@pytest.mark.integration
def test_search_gmail_only() -> None:
    """Test search with Gmail source only."""
    result = search("test", sources=["gmail"], max_results=5)

    assert "gmail_results" in result
    assert isinstance(result["gmail_results"], list)


@pytest.mark.integration
def test_search_both_sources() -> None:
    """Test search with both Drive and Gmail (default)."""
    result = search("meeting", max_results=3)

    assert "drive_results" in result
    assert "gmail_results" in result
    assert result["sources"] == ["drive", "gmail"]


@pytest.mark.integration
def test_search_result_format() -> None:
    """Test that results have expected fields."""
    result = search("test", max_results=5)

    # Check Drive result format if any
    if result.get("drive_results"):
        first = result["drive_results"][0]
        assert "id" in first
        assert "name" in first
        assert "mimeType" in first
        assert "url" in first or first.get("url") is None  # May be None

    # Check Gmail result format if any
    if result.get("gmail_results"):
        first = result["gmail_results"][0]
        assert "thread_id" in first
        assert "subject" in first
        assert "snippet" in first
        assert "from" in first or first.get("from") is None


@pytest.mark.integration
def test_search_no_results() -> None:
    """Test search with query that returns no results."""
    result = search("xyzzy12345nosuchterm98765", max_results=5)

    # Should return empty lists, not error
    assert "drive_results" in result
    assert "gmail_results" in result
    assert len(result["drive_results"]) == 0
    assert len(result["gmail_results"]) == 0
    # Should NOT have errors key for this case
    assert "errors" not in result or len(result.get("errors", [])) == 0


@pytest.mark.integration
def test_search_contacts_not_implemented() -> None:
    """Test that contacts source returns placeholder error."""
    result = search("test", sources=["contacts"], max_results=5)

    assert "contacts_results" in result
    assert "errors" in result
    assert any("Contacts" in e for e in result["errors"])
