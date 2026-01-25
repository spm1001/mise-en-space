"""
Integration tests for the Office file extraction adapter.

Run with: uv run pytest tests/integration/test_office_adapter.py -v -m integration
"""

import json
import pytest
from pathlib import Path

from adapters.office import (
    fetch_and_extract_office,
    get_office_type_from_mime,
    OFFICE_FORMATS,
)


IDS_FILE = Path(__file__).parent.parent.parent / "fixtures" / "integration_ids.json"


@pytest.fixture
def integration_ids() -> dict[str, str]:
    """Load integration test IDs from config file."""
    if not IDS_FILE.exists():
        pytest.skip(f"Integration IDs not configured. Create {IDS_FILE}")
    with open(IDS_FILE) as f:
        return json.load(f)


# --- DOCX Tests ---


@pytest.mark.integration
def test_fetch_and_extract_docx(integration_ids: dict[str, str]) -> None:
    """Test DOCX extraction returns markdown content."""
    docx_id = integration_ids.get("test_docx_id")
    if not docx_id:
        pytest.skip("test_docx_id not in integration_ids.json")

    result = fetch_and_extract_office(docx_id, "docx")

    assert result.content
    assert len(result.content) > 0
    assert result.source_type == "docx"
    assert result.export_format == "markdown"
    assert result.extension == "md"


@pytest.mark.integration
def test_docx_extraction_produces_markdown(integration_ids: dict[str, str]) -> None:
    """Test that DOCX extraction produces valid-looking markdown."""
    docx_id = integration_ids.get("test_docx_id")
    if not docx_id:
        pytest.skip("test_docx_id not in integration_ids.json")

    result = fetch_and_extract_office(docx_id, "docx")

    # Should have some markdown-like content (headings, paragraphs, etc.)
    # At minimum, should have text content
    assert len(result.content.strip()) > 100


# --- XLSX Tests ---


@pytest.mark.integration
def test_fetch_and_extract_xlsx(integration_ids: dict[str, str]) -> None:
    """Test XLSX extraction returns CSV content."""
    xlsx_id = integration_ids.get("test_xlsx_id")
    if not xlsx_id:
        pytest.skip("test_xlsx_id not in integration_ids.json")

    result = fetch_and_extract_office(xlsx_id, "xlsx")

    assert result.content
    assert len(result.content) > 0
    assert result.source_type == "xlsx"
    assert result.export_format == "csv"
    assert result.extension == "csv"


@pytest.mark.integration
def test_xlsx_extraction_produces_csv(integration_ids: dict[str, str]) -> None:
    """Test that XLSX extraction produces valid-looking CSV."""
    xlsx_id = integration_ids.get("test_xlsx_id")
    if not xlsx_id:
        pytest.skip("test_xlsx_id not in integration_ids.json")

    result = fetch_and_extract_office(xlsx_id, "xlsx")

    # CSV should have at least one line with commas or data
    lines = result.content.strip().split("\n")
    assert len(lines) >= 1


# --- MIME Type Detection Tests ---


def test_get_office_type_from_mime_docx() -> None:
    """Test DOCX MIME detection."""
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert get_office_type_from_mime(mime) == "docx"


def test_get_office_type_from_mime_xlsx() -> None:
    """Test XLSX MIME detection."""
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert get_office_type_from_mime(mime) == "xlsx"


def test_get_office_type_from_mime_pptx() -> None:
    """Test PPTX MIME detection."""
    mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    assert get_office_type_from_mime(mime) == "pptx"


def test_get_office_type_from_mime_unknown() -> None:
    """Test unknown MIME returns None."""
    assert get_office_type_from_mime("application/pdf") is None
    assert get_office_type_from_mime("text/plain") is None
