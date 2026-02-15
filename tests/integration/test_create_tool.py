"""
Integration tests for the create MCP tool.

Run with: uv run pytest tests/integration/test_create_tool.py -v -m integration
"""

import pytest

from server import do
from adapters.services import get_drive_service


@pytest.fixture
def cleanup_created_files():
    """Track and delete created files after test."""
    created_ids: list[str] = []
    yield created_ids

    # Cleanup: delete all created files
    if created_ids:
        service = get_drive_service()
        for file_id in created_ids:
            try:
                service.files().delete(fileId=file_id).execute()
            except Exception:
                pass  # Best effort cleanup


@pytest.mark.integration
def test_create_doc_basic(cleanup_created_files: list[str]) -> None:
    """Test creating a basic Google Doc from markdown."""
    content = """# Test Document

This is a test document created by mise-en-space integration tests.

## Features

- Markdown **bold** and *italic*
- Lists work
- [Links](https://example.com) too

## Cleanup

This document should be automatically deleted after the test.
"""
    title = "mise-en-space-test-doc"

    result = do(operation="create", content=content, title=title)

    assert "error" not in result, f"Create failed: {result}"
    assert "file_id" in result
    assert "web_link" in result
    assert result["title"] == title
    assert result["type"] == "doc"

    # Track for cleanup
    cleanup_created_files.append(result["file_id"])

    # Verify the doc exists by fetching its metadata
    service = get_drive_service()
    file_meta = service.files().get(fileId=result["file_id"], fields="id,name,mimeType").execute()
    assert file_meta["name"] == title
    assert file_meta["mimeType"] == "application/vnd.google-apps.document"


@pytest.mark.integration
def test_create_doc_with_folder(cleanup_created_files: list[str]) -> None:
    """Test creating a doc in a specific folder."""
    # First, create a test folder
    service = get_drive_service()
    folder_meta = {
        "name": "mise-en-space-test-folder",
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = service.files().create(body=folder_meta, fields="id").execute()
    folder_id = folder["id"]
    cleanup_created_files.append(folder_id)

    # Now create a doc in that folder
    content = "# Test in Folder\n\nThis doc should be in the test folder."
    title = "mise-en-space-test-doc-in-folder"

    result = do(operation="create", content=content, title=title, folder_id=folder_id)

    assert "error" not in result, f"Create failed: {result}"
    cleanup_created_files.append(result["file_id"])

    # Verify the doc is in the folder
    file_meta = service.files().get(fileId=result["file_id"], fields="parents").execute()
    assert folder_id in file_meta.get("parents", [])


@pytest.mark.integration
def test_create_doc_empty_content(cleanup_created_files: list[str]) -> None:
    """Test that empty content still creates a doc."""
    content = ""
    title = "mise-en-space-empty-test"

    result = do(operation="create", content=content, title=title)

    assert "error" not in result, f"Create failed: {result}"
    assert "file_id" in result
    cleanup_created_files.append(result["file_id"])


@pytest.mark.integration
def test_create_unsupported_type() -> None:
    """Test that unsupported doc_type returns error."""
    result = do(operation="create", content="content", title="title", doc_type="sheet")

    assert "error" in result
    assert result["error"] is True
    assert result["kind"] == "not_implemented"


@pytest.mark.integration
def test_create_invalid_type() -> None:
    """Test that invalid doc_type returns error."""
    result = do(operation="create", content="content", title="title", doc_type="invalid")

    assert "error" in result
    assert result["error"] is True
    assert result["kind"] == "invalid_input"
