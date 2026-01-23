"""
Integration tests for docs adapter.

Run with: uv run pytest tests/integration/test_docs_adapter.py -v
"""

import json
import pytest
from pathlib import Path

from adapters.docs import fetch_document
from extractors.docs import extract_doc_content
from models import DocData


IDS_FILE = Path(__file__).parent.parent.parent / "fixtures" / "integration_ids.json"


@pytest.fixture
def integration_ids() -> dict[str, str]:
    """Load integration test IDs from config file."""
    if not IDS_FILE.exists():
        pytest.skip(
            f"Integration IDs not configured. Create {IDS_FILE} with test_doc_id"
        )
    with open(IDS_FILE) as f:
        return json.load(f)


@pytest.mark.integration
def test_fetch_document_returns_data(integration_ids: dict[str, str]) -> None:
    """Test that fetch_document returns valid DocData."""
    doc_id = integration_ids.get("test_doc_id")
    if not doc_id:
        pytest.skip("test_doc_id not in integration_ids.json")

    result = fetch_document(doc_id)

    assert isinstance(result, DocData)
    assert result.document_id == doc_id
    assert result.title  # Should have a title
    assert len(result.tabs) > 0  # At least one tab


@pytest.mark.integration
def test_fetch_document_has_content(integration_ids: dict[str, str]) -> None:
    """Test that fetched document contains body content."""
    doc_id = integration_ids.get("test_doc_id")
    if not doc_id:
        pytest.skip("test_doc_id not in integration_ids.json")

    result = fetch_document(doc_id)

    # At least one tab should have body content
    has_content = any(
        tab.body.get("content") for tab in result.tabs
    )
    assert has_content, "Expected at least one tab with body content"


@pytest.mark.integration
def test_end_to_end_docs_extraction(integration_ids: dict[str, str]) -> None:
    """Test full flow: adapter → extractor → content."""
    doc_id = integration_ids.get("test_doc_id")
    if not doc_id:
        pytest.skip("test_doc_id not in integration_ids.json")

    # Fetch from API
    data = fetch_document(doc_id)

    # Extract content
    content = extract_doc_content(data)

    # Verify output
    assert isinstance(content, str)
    assert len(content) > 0
    # Should contain some text content
    assert content.strip()


@pytest.mark.integration
def test_invalid_document_id() -> None:
    """Test that invalid ID raises appropriate error."""
    from models import MiseError, ErrorKind

    with pytest.raises(MiseError) as exc_info:
        fetch_document("invalid-id-that-does-not-exist")

    # Should be NOT_FOUND or PERMISSION_DENIED
    assert exc_info.value.kind in (ErrorKind.NOT_FOUND, ErrorKind.PERMISSION_DENIED)
