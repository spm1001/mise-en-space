"""
Integration tests for drive adapter.

Run with: uv run pytest tests/integration/test_drive_adapter.py -v
"""

import json
import pytest
from pathlib import Path

from adapters.drive import (
    get_file_metadata,
    export_file,
    search_files,
    is_google_workspace_file,
    GOOGLE_DOC_MIME,
)
from models import DriveSearchResult


IDS_FILE = Path(__file__).parent.parent.parent / "fixtures" / "integration_ids.json"


@pytest.fixture
def integration_ids() -> dict[str, str]:
    """Load integration test IDs from config file."""
    if not IDS_FILE.exists():
        pytest.skip(
            f"Integration IDs not configured. Create {IDS_FILE}"
        )
    with open(IDS_FILE) as f:
        return json.load(f)


@pytest.mark.integration
def test_get_file_metadata(integration_ids: dict[str, str]) -> None:
    """Test that get_file_metadata returns expected fields."""
    doc_id = integration_ids.get("test_doc_id")
    if not doc_id:
        pytest.skip("test_doc_id not in integration_ids.json")

    result = get_file_metadata(doc_id)

    assert result["id"] == doc_id
    assert "name" in result
    assert "mimeType" in result
    assert result["mimeType"] == GOOGLE_DOC_MIME


@pytest.mark.integration
def test_export_doc_to_markdown(integration_ids: dict[str, str]) -> None:
    """Test exporting a Google Doc to markdown."""
    doc_id = integration_ids.get("test_doc_id")
    if not doc_id:
        pytest.skip("test_doc_id not in integration_ids.json")

    result = export_file(doc_id, "text/markdown")

    assert isinstance(result, bytes)
    assert len(result) > 0
    # Should be valid UTF-8 markdown
    text = result.decode("utf-8")
    assert len(text) > 0


@pytest.mark.integration
def test_search_files_returns_results(integration_ids: dict[str, str]) -> None:
    """Test that search returns results."""
    # Search for test documents
    results = search_files("name contains 'Test'", max_results=5)

    assert isinstance(results, list)
    # May return empty if no matches, but should be a list
    for result in results:
        assert isinstance(result, DriveSearchResult)
        assert result.file_id
        assert result.name
        assert result.mime_type


@pytest.mark.integration
def test_search_files_respects_max_results() -> None:
    """Test that max_results limits results."""
    results = search_files("mimeType != 'application/vnd.google-apps.folder'", max_results=3)

    assert len(results) <= 3


@pytest.mark.integration
def test_invalid_file_id() -> None:
    """Test that invalid ID raises appropriate error."""
    from models import MiseError, ErrorKind

    with pytest.raises(MiseError) as exc_info:
        get_file_metadata("invalid-id-that-does-not-exist")

    # Should be NOT_FOUND or PERMISSION_DENIED
    assert exc_info.value.kind in (ErrorKind.NOT_FOUND, ErrorKind.PERMISSION_DENIED)


def test_is_google_workspace_file() -> None:
    """Test MIME type detection utility."""
    assert is_google_workspace_file(GOOGLE_DOC_MIME)
    assert is_google_workspace_file("application/vnd.google-apps.spreadsheet")
    assert not is_google_workspace_file("application/pdf")
    assert not is_google_workspace_file("text/plain")
