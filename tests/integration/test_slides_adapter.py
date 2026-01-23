"""
Integration tests for slides adapter.

Run with: uv run pytest tests/integration/test_slides_adapter.py -v
"""

import json
import pytest
from pathlib import Path

from adapters.slides import fetch_presentation
from extractors.slides import extract_slides_content
from models import PresentationData


IDS_FILE = Path(__file__).parent.parent.parent / "fixtures" / "integration_ids.json"


@pytest.fixture
def integration_ids() -> dict[str, str]:
    """Load integration test IDs from config file."""
    if not IDS_FILE.exists():
        pytest.skip(
            f"Integration IDs not configured. Create {IDS_FILE} with test_presentation_id"
        )
    with open(IDS_FILE) as f:
        return json.load(f)


@pytest.mark.integration
def test_fetch_presentation_returns_data(integration_ids: dict[str, str]) -> None:
    """Test that fetch_presentation returns valid PresentationData."""
    pres_id = integration_ids.get("test_presentation_id")
    if not pres_id:
        pytest.skip("test_presentation_id not in integration_ids.json")

    result = fetch_presentation(pres_id, include_thumbnails=False)

    assert isinstance(result, PresentationData)
    assert result.presentation_id == pres_id
    assert result.title  # Should have a title
    assert len(result.slides) > 0  # At least one slide


@pytest.mark.integration
def test_fetch_presentation_with_thumbnails(integration_ids: dict[str, str]) -> None:
    """Test that thumbnails are fetched when requested."""
    pres_id = integration_ids.get("test_presentation_id")
    if not pres_id:
        pytest.skip("test_presentation_id not in integration_ids.json")

    result = fetch_presentation(pres_id, include_thumbnails=True)

    assert result.thumbnails_included
    # At least one slide should have thumbnail bytes
    has_thumbnails = any(
        slide.thumbnail_bytes for slide in result.slides
    )
    assert has_thumbnails, "Expected at least one slide with thumbnail"


@pytest.mark.integration
def test_end_to_end_slides_extraction(integration_ids: dict[str, str]) -> None:
    """Test full flow: adapter → extractor → content."""
    pres_id = integration_ids.get("test_presentation_id")
    if not pres_id:
        pytest.skip("test_presentation_id not in integration_ids.json")

    # Fetch from API (without thumbnails for speed)
    data = fetch_presentation(pres_id, include_thumbnails=False)

    # Extract content
    content = extract_slides_content(data)

    # Verify output
    assert isinstance(content, str)
    assert len(content) > 0
    assert f"**Slides:** {len(data.slides)}" in content


@pytest.mark.integration
def test_invalid_presentation_id() -> None:
    """Test that invalid ID raises appropriate error."""
    from models import MiseError, ErrorKind

    with pytest.raises(MiseError) as exc_info:
        fetch_presentation("invalid-id-that-does-not-exist")

    # Should be NOT_FOUND, PERMISSION_DENIED, or INVALID_INPUT (HTTP 400)
    assert exc_info.value.kind in (
        ErrorKind.NOT_FOUND,
        ErrorKind.PERMISSION_DENIED,
        ErrorKind.INVALID_INPUT,
        ErrorKind.UNKNOWN,  # 400 errors map to UNKNOWN currently
    )
