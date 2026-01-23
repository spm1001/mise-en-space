"""
Integration tests for sheets adapter.

Run with: uv run pytest tests/integration/test_sheets_adapter.py -v
"""

import json
import pytest
from pathlib import Path

from adapters.sheets import fetch_spreadsheet
from extractors.sheets import extract_sheets_content
from models import SpreadsheetData


IDS_FILE = Path(__file__).parent.parent.parent / "fixtures" / "integration_ids.json"


@pytest.fixture
def integration_ids() -> dict[str, str]:
    """Load integration test IDs from config file."""
    if not IDS_FILE.exists():
        pytest.skip(
            f"Integration IDs not configured. Create {IDS_FILE} with test_sheet_id"
        )
    with open(IDS_FILE) as f:
        return json.load(f)


@pytest.mark.integration
def test_fetch_spreadsheet_returns_data(integration_ids: dict[str, str]) -> None:
    """Test that fetch_spreadsheet returns valid SpreadsheetData."""
    sheet_id = integration_ids.get("test_sheet_id")
    if not sheet_id:
        pytest.skip("test_sheet_id not in integration_ids.json")

    result = fetch_spreadsheet(sheet_id)

    assert isinstance(result, SpreadsheetData)
    assert result.spreadsheet_id == sheet_id
    assert result.title  # Should have a title
    assert len(result.sheets) > 0  # At least one sheet


@pytest.mark.integration
def test_fetch_spreadsheet_has_values(integration_ids: dict[str, str]) -> None:
    """Test that fetched spreadsheet contains cell values."""
    sheet_id = integration_ids.get("test_sheet_id")
    if not sheet_id:
        pytest.skip("test_sheet_id not in integration_ids.json")

    result = fetch_spreadsheet(sheet_id)

    # At least one sheet should have data
    has_data = any(len(sheet.values) > 0 for sheet in result.sheets)
    assert has_data, "Expected at least one sheet with data"


@pytest.mark.integration
def test_end_to_end_sheets_extraction(integration_ids: dict[str, str]) -> None:
    """Test full flow: adapter → extractor → content."""
    sheet_id = integration_ids.get("test_sheet_id")
    if not sheet_id:
        pytest.skip("test_sheet_id not in integration_ids.json")

    # Fetch from API
    data = fetch_spreadsheet(sheet_id)

    # Extract content
    content = extract_sheets_content(data)

    # Verify output
    assert isinstance(content, str)
    assert len(content) > 0
    # Sheet names should appear in output (as === Sheet: Name === headers)
    assert any(f"=== Sheet: {sheet.name} ===" in content for sheet in data.sheets)


@pytest.mark.integration
def test_invalid_spreadsheet_id() -> None:
    """Test that invalid ID raises appropriate error."""
    from models import MiseError, ErrorKind

    with pytest.raises(MiseError) as exc_info:
        fetch_spreadsheet("invalid-id-that-does-not-exist")

    # Should be NOT_FOUND or PERMISSION_DENIED
    assert exc_info.value.kind in (ErrorKind.NOT_FOUND, ErrorKind.PERMISSION_DENIED)
