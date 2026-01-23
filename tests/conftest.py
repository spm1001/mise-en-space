"""
Shared pytest fixtures for mise-en-space tests.

Fixtures are loaded from the fixtures/ directory at project root.
JSON is converted to typed dataclasses for type safety.
"""

import json
from pathlib import Path

import pytest

from models import SpreadsheetData, SheetTab, DocData, DocTab

# Project root for fixture loading
PROJECT_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = PROJECT_ROOT / "fixtures"


def load_fixture(category: str, name: str) -> dict:
    """
    Load a JSON fixture by category and name.

    Args:
        category: Subdirectory (sheets, docs, gmail, slides)
        name: Fixture name without extension

    Example:
        load_fixture("sheets", "basic")  # loads fixtures/sheets/basic.json
    """
    fixture_path = FIXTURES_DIR / category / f"{name}.json"
    with open(fixture_path) as f:
        return json.load(f)


# ============================================================================
# Sheets Fixtures
# ============================================================================

@pytest.fixture
def sheets_response() -> SpreadsheetData:
    """Sample Google Sheets data for testing."""
    raw = load_fixture("sheets", "basic")
    return SpreadsheetData(
        title=raw["title"],
        spreadsheet_id="test-spreadsheet-id",
        sheets=[
            SheetTab(name=s["name"], values=s["values"])
            for s in raw["sheets"]
        ],
    )


# ============================================================================
# Docs Fixtures
# ============================================================================

@pytest.fixture
def docs_response() -> DocData:
    """Sample Google Docs data for testing."""
    raw = load_fixture("docs", "basic")
    return DocData(
        title=raw["title"],
        document_id=raw["document_id"],
        tabs=[
            DocTab(
                title=t["title"],
                tab_id=t["tab_id"],
                index=t["index"],
                body=t["body"],
                footnotes=t.get("footnotes", {}),
                lists=t.get("lists", {}),
                inline_objects=t.get("inline_objects", {}),
            )
            for t in raw["tabs"]
        ],
    )


# ============================================================================
# Future fixtures (add as extractors are ported)
# ============================================================================

# @pytest.fixture
# def gmail_thread_response() -> dict:
#     """Sample Gmail thread API response."""
#     return load_fixture("gmail_thread_response")

# @pytest.fixture
# def slides_response() -> dict:
#     """Sample Google Slides API response."""
#     return load_fixture("slides_response")
