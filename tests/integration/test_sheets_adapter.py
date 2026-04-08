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


# --- Merged cell resolution tests ---


@pytest.mark.integration
def test_horizontal_header_merges(integration_ids: dict[str, str]) -> None:
    """Horizontal merges propagate the top-left value across all columns."""
    sheet_id = integration_ids.get("test_sheet_merged_cells_id")
    if not sheet_id:
        pytest.skip("test_sheet_merged_cells_id not in integration_ids.json")

    data = fetch_spreadsheet(sheet_id, tabs=["Horizontal Headers"])
    content = extract_sheets_content(data)

    # "Q1 2026" should appear 3 times in the header row (B1:D1)
    lines = content.strip().splitlines()
    header = lines[1]  # line 0 is === Sheet: ... ===
    assert header.count("Q1 2026") == 3, f"Expected Q1 2026 propagated 3x, got: {header}"
    assert header.count("Q2 2026") == 3, f"Expected Q2 2026 propagated 3x, got: {header}"


@pytest.mark.integration
def test_vertical_merges(integration_ids: dict[str, str]) -> None:
    """Vertical merges propagate the top-left value down all rows."""
    sheet_id = integration_ids.get("test_sheet_merged_cells_id")
    if not sheet_id:
        pytest.skip("test_sheet_merged_cells_id not in integration_ids.json")

    data = fetch_spreadsheet(sheet_id, tabs=["Vertical Merges"])
    content = extract_sheets_content(data)

    lines = content.strip().splitlines()
    # Skip header line (=== Sheet:) and column header row
    data_lines = lines[2:]  # Fruit/Apple, Fruit/Banana, Fruit/Cherry, Vegetable/Carrot, Vegetable/Daikon
    fruit_lines = [l for l in data_lines if "Fruit" in l]
    veg_lines = [l for l in data_lines if "Vegetable" in l]
    assert len(fruit_lines) == 3, f"Expected Fruit in 3 rows, got {len(fruit_lines)}: {fruit_lines}"
    assert len(veg_lines) == 2, f"Expected Vegetable in 2 rows, got {len(veg_lines)}: {veg_lines}"


@pytest.mark.integration
def test_mixed_merges_with_formulas(integration_ids: dict[str, str]) -> None:
    """Merges resolve correctly alongside formula-evaluated cells."""
    sheet_id = integration_ids.get("test_sheet_merged_cells_id")
    if not sheet_id:
        pytest.skip("test_sheet_merged_cells_id not in integration_ids.json")

    data = fetch_spreadsheet(sheet_id, tabs=["Mixed Merges + Formulas"])
    content = extract_sheets_content(data)

    lines = content.strip().splitlines()
    data_lines = lines[2:]  # Skip sheet header and column header

    # Both Alpha rows should have "Alpha" in column A
    alpha_lines = [l for l in data_lines if "Alpha" in l]
    assert len(alpha_lines) == 2, f"Expected Alpha in 2 rows, got {len(alpha_lines)}"

    # Formula results: 10+20=30, 30+40=70, 50+60=110, 70+80=150
    assert "30" in data_lines[0]
    assert "70" in data_lines[1]
    assert "110" in data_lines[2]
    assert "150" in data_lines[3]


@pytest.mark.integration
def test_merged_cell_count_in_warnings(integration_ids: dict[str, str]) -> None:
    """Spreadsheet with merges includes a warning about resolved merges."""
    sheet_id = integration_ids.get("test_sheet_merged_cells_id")
    if not sheet_id:
        pytest.skip("test_sheet_merged_cells_id not in integration_ids.json")

    data = fetch_spreadsheet(sheet_id)
    assert data.merged_cell_count == 9, f"Expected 9 merges, got {data.merged_cell_count}"
