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
def test_create_sheet_basic(cleanup_created_files: list[str]) -> None:
    """Test creating a basic Google Sheet from CSV."""
    csv_content = "Name,Amount\nAlice,100\nBob,200"
    title = "mise-en-space-test-sheet"

    result = do(operation="create", content=csv_content, title=title, doc_type="sheet")

    assert "error" not in result, f"Create failed: {result}"
    assert "file_id" in result
    assert "web_link" in result
    assert result["title"] == title
    assert result["type"] == "sheet"

    cleanup_created_files.append(result["file_id"])

    # Verify it's a spreadsheet
    service = get_drive_service()
    file_meta = service.files().get(fileId=result["file_id"], fields="id,name,mimeType").execute()
    assert file_meta["name"] == title
    assert file_meta["mimeType"] == "application/vnd.google-apps.spreadsheet"


@pytest.mark.integration
def test_create_sheet_currency_with_commas(cleanup_created_files: list[str]) -> None:
    """Sheet with UK currency containing commas (£65,000) renders correctly."""
    csv_content = 'Department,Budget\nEngineering,"£65,000"\nMarketing,"£42,500"\nTotal,"£107,500"'
    title = "mise-en-space-test-currency"

    result = do(operation="create", content=csv_content, title=title, doc_type="sheet")

    assert "error" not in result, f"Create failed: {result}"
    cleanup_created_files.append(result["file_id"])

    # Read back via Sheets API to verify values survived
    from adapters.services import get_sheets_service
    sheets = get_sheets_service()
    data = sheets.spreadsheets().values().get(
        spreadsheetId=result["file_id"], range="A1:B4"
    ).execute()
    values = data.get("values", [])
    assert values[0] == ["Department", "Budget"]
    assert values[1][1] == "£65,000"  # Commas preserved inside quotes


@pytest.mark.integration
def test_create_sheet_leading_zeros(cleanup_created_files: list[str]) -> None:
    """Sheet with tick-prefixed leading zeros preserves them as text."""
    csv_content = "ID,Name\n'00412,Alice\n'00089,Bob"
    title = "mise-en-space-test-leading-zeros"

    result = do(operation="create", content=csv_content, title=title, doc_type="sheet")

    assert "error" not in result, f"Create failed: {result}"
    cleanup_created_files.append(result["file_id"])

    # Read back — tick prefix tells Google to treat as text
    from adapters.services import get_sheets_service
    sheets = get_sheets_service()
    data = sheets.spreadsheets().values().get(
        spreadsheetId=result["file_id"], range="A1:B3"
    ).execute()
    values = data.get("values", [])
    # Drive CSV import should preserve the leading zeros
    assert values[1][0] in ("00412", "'00412")  # Either form is acceptable
    assert values[2][0] in ("00089", "'00089")


@pytest.mark.integration
def test_create_sheet_with_formulae(cleanup_created_files: list[str]) -> None:
    """Sheet with formulae — Drive CSV import preserves formula syntax."""
    csv_content = "A,B,Sum\n10,20,=A2+B2\n30,40,=A3+B3"
    title = "mise-en-space-test-formulae"

    result = do(operation="create", content=csv_content, title=title, doc_type="sheet")

    assert "error" not in result, f"Create failed: {result}"
    cleanup_created_files.append(result["file_id"])

    # Read back with valueRenderOption=FORMULA to see if formulae survived
    from adapters.services import get_sheets_service
    sheets = get_sheets_service()
    data = sheets.spreadsheets().values().get(
        spreadsheetId=result["file_id"], range="C2:C3",
        valueRenderOption="FORMULA",
    ).execute()
    values = data.get("values", [])
    # Drive CSV import should keep formulae as formulae
    assert any("=" in str(v) for row in values for v in row), f"No formulae found in {values}"


@pytest.mark.integration
def test_create_invalid_type() -> None:
    """Test that invalid doc_type returns error."""
    result = do(operation="create", content="content", title="title", doc_type="invalid")

    assert "error" in result
    assert result["error"] is True
    assert result["kind"] == "invalid_input"
