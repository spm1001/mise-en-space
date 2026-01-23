"""
Integration tests for gmail adapter.

Run with: uv run pytest tests/integration/test_gmail_adapter.py -v
"""

import json
import pytest
from pathlib import Path

from adapters.gmail import fetch_thread, fetch_message, search_threads
from extractors.gmail import extract_thread_content
from models import GmailThreadData, GmailSearchResult, EmailMessage


IDS_FILE = Path(__file__).parent.parent.parent / "fixtures" / "integration_ids.json"


@pytest.fixture
def integration_ids() -> dict[str, str]:
    """Load integration test IDs from config file."""
    if not IDS_FILE.exists():
        pytest.skip(
            f"Integration IDs not configured. Create {IDS_FILE} with test_thread_id"
        )
    with open(IDS_FILE) as f:
        return json.load(f)


@pytest.mark.integration
def test_fetch_thread_returns_data(integration_ids: dict[str, str]) -> None:
    """Test that fetch_thread returns valid GmailThreadData."""
    thread_id = integration_ids.get("test_thread_id")
    if not thread_id:
        pytest.skip("test_thread_id not in integration_ids.json")

    result = fetch_thread(thread_id)

    assert isinstance(result, GmailThreadData)
    assert result.thread_id == thread_id
    assert len(result.messages) > 0  # At least one message


@pytest.mark.integration
def test_fetch_thread_has_message_content(integration_ids: dict[str, str]) -> None:
    """Test that fetched thread contains message bodies."""
    thread_id = integration_ids.get("test_thread_id")
    if not thread_id:
        pytest.skip("test_thread_id not in integration_ids.json")

    result = fetch_thread(thread_id)

    # At least one message should have body content
    has_body = any(
        msg.body_text or msg.body_html for msg in result.messages
    )
    assert has_body, "Expected at least one message with body content"


@pytest.mark.integration
def test_fetch_thread_parses_headers(integration_ids: dict[str, str]) -> None:
    """Test that message headers are correctly parsed."""
    thread_id = integration_ids.get("test_thread_id")
    if not thread_id:
        pytest.skip("test_thread_id not in integration_ids.json")

    result = fetch_thread(thread_id)

    # First message should have headers
    first_msg = result.messages[0]
    assert first_msg.from_address  # Should have From
    assert first_msg.subject or result.subject  # Should have subject somewhere


@pytest.mark.integration
def test_end_to_end_gmail_extraction(integration_ids: dict[str, str]) -> None:
    """Test full flow: adapter → extractor → content."""
    thread_id = integration_ids.get("test_thread_id")
    if not thread_id:
        pytest.skip("test_thread_id not in integration_ids.json")

    # Fetch from API
    data = fetch_thread(thread_id)

    # Extract content
    content = extract_thread_content(data)

    # Verify output
    assert isinstance(content, str)
    assert len(content) > 0
    # Should start with subject header
    assert content.startswith("# ")
    # Should contain message headers like [1/N]
    assert "[1/" in content


@pytest.mark.integration
def test_fetch_single_message(integration_ids: dict[str, str]) -> None:
    """Test fetching a single message by ID."""
    thread_id = integration_ids.get("test_thread_id")
    if not thread_id:
        pytest.skip("test_thread_id not in integration_ids.json")

    # First get thread to find a message ID
    thread = fetch_thread(thread_id)
    message_id = thread.messages[0].message_id

    # Fetch single message
    result = fetch_message(message_id)

    assert isinstance(result, EmailMessage)
    assert result.message_id == message_id


@pytest.mark.integration
def test_invalid_thread_id() -> None:
    """Test that invalid ID raises appropriate error."""
    from models import MiseError, ErrorKind

    with pytest.raises(MiseError) as exc_info:
        fetch_thread("invalid-id-that-does-not-exist")

    # Should be NOT_FOUND, PERMISSION_DENIED, or INVALID_INPUT (HTTP 400)
    assert exc_info.value.kind in (
        ErrorKind.NOT_FOUND,
        ErrorKind.PERMISSION_DENIED,
        ErrorKind.INVALID_INPUT,
        ErrorKind.UNKNOWN,  # 400 errors map to UNKNOWN currently
    )


@pytest.mark.integration
def test_search_threads_returns_results() -> None:
    """Test that search_threads returns valid results."""
    # Search for something that should exist in any Gmail account
    results = search_threads("in:inbox", max_results=5)

    assert isinstance(results, list)
    # May or may not have results depending on inbox state
    if results:
        first = results[0]
        assert isinstance(first, GmailSearchResult)
        assert first.thread_id  # Has ID
        assert first.subject or first.snippet  # Has some content


@pytest.mark.integration
def test_search_threads_with_query() -> None:
    """Test search with a specific query."""
    # Search for a common term
    results = search_threads("test", max_results=3)

    assert isinstance(results, list)
    for result in results:
        assert isinstance(result, GmailSearchResult)
        assert result.thread_id
        assert result.message_count >= 1


@pytest.mark.integration
def test_search_threads_empty_query() -> None:
    """Test search with query that likely returns nothing."""
    # Highly specific query unlikely to match
    results = search_threads("xyzzy12345nosuchterm98765", max_results=5)

    assert isinstance(results, list)
    assert len(results) == 0  # Should find nothing
