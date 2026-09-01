"""
Integration tests for anchored do(comment) (mise-jupuja).

**These write nothing.** Every test here exercises a read or a refusal, so the
suite can run repeatedly without littering anyone's Drive with comment threads.
The write legs were verified live once, on 2026-09-01, against the picihi scratch
artefacts; that measurement is recorded in the bon note and the handoff rather
than repeated on every CI run.

What they are for is the class of bug a stubbed unit test structurally cannot
see: a field mask is only ever judged by Google. The Docs anchor read shipped
with a mask asking for both `tabs` and `body`, which is rejected outright — every
unit test passed, and the first live call failed. That is what
`test_document_anchor_read_is_a_legal_field_mask` exists to catch next time.

Run with: uv run pytest tests/integration/test_anchored_comment.py -v
"""

import json
from pathlib import Path

import pytest

from adapters.anchored_comments import (
    read_document_for_anchoring,
    read_presentation_slides,
    read_spreadsheet_tabs,
)
from extractors.comment_anchors import locate_quote
from tools.comment import do_comment

IDS_FILE = Path(__file__).parent.parent.parent / "fixtures" / "integration_ids.json"


@pytest.fixture
def integration_ids() -> dict[str, str]:
    if not IDS_FILE.exists():
        pytest.skip(f"Integration IDs not configured — create {IDS_FILE}")
    with open(IDS_FILE) as f:
        return json.load(f)


@pytest.mark.integration
def test_document_anchor_read_is_a_legal_field_mask(integration_ids: dict[str, str]) -> None:
    """The mask, the view mode and includeTabsContent have to be legal together.

    Google refuses `tabs` and `body` in one mask while requesting tabs content,
    and refuses comments entirely while previewing suggestions. Only a live call
    can tell you.
    """
    doc_id = integration_ids.get("test_doc_with_comments_id") or integration_ids.get("test_doc_id")
    if not doc_id:
        pytest.skip("no test doc configured")

    payload = read_document_for_anchoring(doc_id)
    assert payload.get("revisionId"), "no revisionId — the write cannot be pinned"
    assert payload.get("tabs"), "no tabs content — quote resolution would find nothing"


@pytest.mark.integration
def test_deck_and_workbook_anchor_reads(integration_ids: dict[str, str]) -> None:
    deck_id = integration_ids.get("test_deck_with_ui_comment_id") or integration_ids.get("test_presentation_id")
    sheet_id = integration_ids.get("test_sheet_with_anchored_comment_id") or integration_ids.get("test_sheet_id")
    if not (deck_id and sheet_id):
        pytest.skip("no deck/sheet configured")

    deck = read_presentation_slides(deck_id)
    assert deck.get("slides"), "no slides — 'slide N' could not resolve"
    assert deck.get("revisionId"), "no revisionId — the write cannot be pinned"

    book = read_spreadsheet_tabs(sheet_id)
    tabs = book.get("sheets") or []
    assert tabs, "no tabs — 'Tab!A1' could not resolve"
    props = tabs[0].get("properties") or {}
    assert props.get("title") and props.get("gridProperties"), (
        "a tab without a title or grid bounds cannot be validated against"
    )


@pytest.mark.integration
def test_quote_resolution_finds_real_document_text(integration_ids: dict[str, str]) -> None:
    """Round-trips a real sentence out of the live document and back through the
    matcher — the whitespace normalisation has to survive real Docs content,
    which splits sentences across runs in ways no fixture reproduces."""
    doc_id = integration_ids.get("test_doc_with_comments_id") or integration_ids.get("test_doc_id")
    if not doc_id:
        pytest.skip("no test doc configured")

    payload = read_document_for_anchoring(doc_id)
    sentence = ""
    for tab in payload.get("tabs") or []:
        for element in ((tab.get("documentTab") or {}).get("body") or {}).get("content") or []:
            for run in ((element.get("paragraph") or {}).get("elements") or []):
                text = ((run.get("textRun") or {}).get("content") or "").strip()
                if len(text) > 25:
                    sentence = text[:25]
                    break
            if sentence:
                break
        if sentence:
            break
    if not sentence:
        pytest.skip("no long-enough run in the test document")

    assert locate_quote(payload, sentence), f"{sentence!r} is in the doc but did not resolve"


@pytest.mark.integration
def test_a_missing_quote_refuses_without_writing(integration_ids: dict[str, str]) -> None:
    """The full do() path against the live API, ending in a refusal — so it
    exercises metadata, the anchor read and the resolution, and creates nothing."""
    doc_id = integration_ids.get("test_doc_with_comments_id") or integration_ids.get("test_doc_id")
    if not doc_id:
        pytest.skip("no test doc configured")

    result = do_comment(
        file_id=doc_id, content="this must never be written",
        anchor="zzq-a-string-no-document-would-contain-9f3c",
    )
    assert isinstance(result, dict) and result.get("error") is True
    # Which refusal fires depends on the document: a single-tab doc reaches the
    # quote search and misses, a multi-tab one is declined before that (the
    # configured test doc has two tabs, so both branches are live in practice).
    assert ("was not found" in result["message"]) or ("tabs" in result["message"])
    assert "Drop anchor=" in result["message"], "a refusal must always name the exit"
