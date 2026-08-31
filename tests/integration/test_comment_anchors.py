"""
Integration tests for Slides/Sheets comment locators (mise-dukacu).

These hit the real Developer Preview endpoints. They are the only check that can
fail for the reason that actually matters — enrollment ending, or Google
changing the preview surface before GA — so a failure here is information, not
noise: it means the flat-render fallback is now the live behaviour.

Test data: the two probe artefacts from the 2026-08-31 DPP probe. The deck
carries Sameer's hand-made UI comment (a shape anchor with a character range),
the sheet carries an API-created B2 anchor. Record their ids in
`fixtures/integration_ids.json` as `test_deck_with_ui_comment_id` and
`test_sheet_with_anchored_comment_id`.

Run with: uv run pytest tests/integration/test_comment_anchors.py -v
"""

import json
from pathlib import Path

import pytest

from adapters.comment_anchors import (
    fetch_sheets_comment_anchors,
    fetch_slides_comment_anchors,
)
from extractors.comment_anchors import sheets_locators, slides_locators

IDS_FILE = Path(__file__).parent.parent.parent / "fixtures" / "integration_ids.json"


@pytest.fixture
def integration_ids() -> dict[str, str]:
    if not IDS_FILE.exists():
        pytest.skip(f"Integration IDs not configured — create {IDS_FILE}")
    with open(IDS_FILE) as f:
        return json.load(f)


@pytest.mark.integration
def test_slides_preview_read_locates_a_ui_comment(integration_ids: dict[str, str]) -> None:
    deck_id = integration_ids.get("test_deck_with_ui_comment_id")
    if not deck_id:
        pytest.skip("test_deck_with_ui_comment_id not in integration_ids.json")

    read = fetch_slides_comment_anchors(deck_id)
    if read.payload is None:
        pytest.fail(
            "Preview read refused — mise would now degrade to the flat render "
            f"for every caller on this token. Reason: {read.reason}"
        )

    locators = slides_locators(read.payload)
    assert locators, "no comment resolved to a slide"
    assert all(loc.label.startswith("slide ") for loc in locators.values() if not loc.orphaned)
    assert any(loc.quote for loc in locators.values()), (
        "no thread carried plainTextQuote — the anchored text is the only anchor "
        "context Slides comments have"
    )


@pytest.mark.integration
def test_sheets_preview_read_locates_a_cell(integration_ids: dict[str, str]) -> None:
    sheet_id = integration_ids.get("test_sheet_with_anchored_comment_id")
    if not sheet_id:
        pytest.skip("test_sheet_with_anchored_comment_id not in integration_ids.json")

    read = fetch_sheets_comment_anchors(sheet_id)
    if read.payload is None:
        pytest.fail(f"Preview read refused: {read.reason}")

    locators = sheets_locators(read.payload)
    assert locators, "no comment resolved to a cell"
    assert all("!" in loc.label for loc in locators.values() if not loc.orphaned), (
        f"expected tab!A1 locators, got {[l.label for l in locators.values()]}"
    )


@pytest.mark.integration
def test_an_invalid_view_mode_degrades_with_a_reason(integration_ids: dict[str, str]) -> None:
    """The live degradation control. Google's own refusal — not a stubbed one —
    has to arrive as a reason string, because that string is what a non-enrolled
    caller sees in their cues instead of locators."""
    import adapters.comment_anchors as ca

    deck_id = integration_ids.get("test_deck_with_ui_comment_id")
    if not deck_id:
        pytest.skip("test_deck_with_ui_comment_id not in integration_ids.json")

    original = ca._COMMENTS_VIEW_MODE
    ca._COMMENTS_VIEW_MODE = "COMMENTS_VIEW_MODE_NOT_A_REAL_VALUE"
    try:
        read = ca.fetch_slides_comment_anchors(deck_id)
    finally:
        ca._COMMENTS_VIEW_MODE = original

    assert read.payload is None
    assert read.reason and "HTTP 4" in read.reason
