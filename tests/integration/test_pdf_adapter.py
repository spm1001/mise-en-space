"""
Integration tests for the PDF extraction adapter.

Run with: uv run pytest tests/integration/test_pdf_adapter.py -v -m integration
"""

import json
import pytest
from pathlib import Path

from adapters.pdf import fetch_and_extract_pdf, DEFAULT_MIN_CHARS_THRESHOLD


IDS_FILE = Path(__file__).parent.parent.parent / "fixtures" / "integration_ids.json"


@pytest.fixture
def integration_ids() -> dict[str, str]:
    """Load integration test IDs from config file."""
    if not IDS_FILE.exists():
        pytest.skip(f"Integration IDs not configured. Create {IDS_FILE}")
    with open(IDS_FILE) as f:
        return json.load(f)


@pytest.mark.integration
def test_fetch_and_extract_pdf_returns_content(integration_ids: dict[str, str]) -> None:
    """Test that PDF extraction returns content."""
    pdf_id = integration_ids.get("test_pdf_id")
    if not pdf_id:
        pytest.skip("test_pdf_id not in integration_ids.json")

    result = fetch_and_extract_pdf(pdf_id)

    assert result.content
    assert len(result.content) > 0
    assert result.method in ("markitdown", "drive")
    assert result.char_count > 0


@pytest.mark.integration
def test_pdf_extraction_method_reported(integration_ids: dict[str, str]) -> None:
    """Test that extraction method is correctly reported."""
    pdf_id = integration_ids.get("test_pdf_id")
    if not pdf_id:
        pytest.skip("test_pdf_id not in integration_ids.json")

    result = fetch_and_extract_pdf(pdf_id)

    # Method should match the threshold logic
    if result.char_count >= DEFAULT_MIN_CHARS_THRESHOLD and result.method == "markitdown":
        # Markitdown succeeded
        assert len(result.warnings) == 0 or not any("falling back" in w for w in result.warnings)
    elif result.method == "drive":
        # Drive fallback was used
        assert any("falling back" in w.lower() for w in result.warnings)


@pytest.mark.integration
def test_pdf_extraction_with_high_threshold(integration_ids: dict[str, str]) -> None:
    """Test that setting a very high threshold forces Drive fallback."""
    pdf_id = integration_ids.get("test_pdf_id")
    if not pdf_id:
        pytest.skip("test_pdf_id not in integration_ids.json")

    # Set threshold impossibly high to force Drive fallback
    result = fetch_and_extract_pdf(pdf_id, min_chars_threshold=1_000_000)

    # Should have fallen back to Drive
    assert result.method == "drive"
    assert any("falling back" in w.lower() for w in result.warnings)


@pytest.mark.integration
def test_pdf_extraction_with_zero_threshold(integration_ids: dict[str, str]) -> None:
    """Test that setting threshold to 0 always uses markitdown."""
    pdf_id = integration_ids.get("test_pdf_id")
    if not pdf_id:
        pytest.skip("test_pdf_id not in integration_ids.json")

    # Set threshold to 0 - markitdown always "succeeds"
    result = fetch_and_extract_pdf(pdf_id, min_chars_threshold=0)

    # Should use markitdown (unless it extracts literally nothing)
    if result.char_count > 0:
        assert result.method == "markitdown"
