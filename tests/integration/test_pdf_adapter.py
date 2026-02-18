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


# --- Local rendering tests (no Google API, needs poppler-utils) ---

from adapters.pdf import render_pdf_pages

FIXTURE_PDF = Path(__file__).parent.parent.parent / "fixtures" / "pdf" / "two_pages.pdf"
PNG_HEADER = b"\x89PNG\r\n\x1a\n"


@pytest.mark.integration
def test_render_pdf_pages_from_file_path() -> None:
    """Render a real 2-page PDF via poppler and verify PNG output."""
    if not FIXTURE_PDF.exists():
        pytest.skip(f"Fixture missing: {FIXTURE_PDF}")

    result = render_pdf_pages(file_path=FIXTURE_PDF)

    assert result.method == "pdf2image"  # Linux — no CoreGraphics
    assert result.page_count == 2
    assert len(result.pages) == 2

    for i, page in enumerate(result.pages):
        assert page.page_index == i
        assert page.image_bytes[:8] == PNG_HEADER, f"Page {i} is not valid PNG"
        assert page.width_px > 0
        assert page.height_px > 0
        # Linux uses fixed 150 DPI (slight overshoot vs 1568px target — API downscales).
        # Allow up to 1800px to cover A4 at 150 DPI (1754px).
        assert max(page.width_px, page.height_px) <= 1800


@pytest.mark.integration
def test_render_pdf_pages_from_bytes() -> None:
    """Render from raw bytes (the other input path)."""
    if not FIXTURE_PDF.exists():
        pytest.skip(f"Fixture missing: {FIXTURE_PDF}")

    pdf_bytes = FIXTURE_PDF.read_bytes()
    result = render_pdf_pages(file_bytes=pdf_bytes)

    assert result.page_count == 2
    assert len(result.pages) == 2
    assert all(p.image_bytes[:8] == PNG_HEADER for p in result.pages)


@pytest.mark.integration
def test_render_pdf_pages_consistent_between_paths() -> None:
    """Both input paths should produce identical page dimensions."""
    if not FIXTURE_PDF.exists():
        pytest.skip(f"Fixture missing: {FIXTURE_PDF}")

    from_path = render_pdf_pages(file_path=FIXTURE_PDF)
    from_bytes = render_pdf_pages(file_bytes=FIXTURE_PDF.read_bytes())

    assert len(from_path.pages) == len(from_bytes.pages)
    for p, b in zip(from_path.pages, from_bytes.pages):
        assert p.width_px == b.width_px
        assert p.height_px == b.height_px
