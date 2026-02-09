"""
Tests for docs adapter using mocked services and real fixtures.

Mocks the Docs API service, feeds real fixture data,
and verifies the adapter parses into DocData correctly.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from models import DocData
from adapters.docs import fetch_document, _build_tab, _build_legacy_tab


FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"


# ============================================================================
# PURE HELPERS
# ============================================================================

class TestBuildTab:
    """Test tab construction from API data."""

    def test_basic_tab(self) -> None:
        tab_data = {
            "tabProperties": {"tabId": "t.0", "title": "Main", "index": 0},
            "documentTab": {
                "body": {"content": [{"paragraph": {}}]},
                "footnotes": {"fn1": {}},
                "lists": {"list1": {}},
                "inlineObjects": {"obj1": {}},
            },
        }
        tab = _build_tab(tab_data, 0)

        assert tab.title == "Main"
        assert tab.tab_id == "t.0"
        assert tab.index == 0
        assert tab.body == {"content": [{"paragraph": {}}]}
        assert "fn1" in tab.footnotes
        assert "list1" in tab.lists
        assert "obj1" in tab.inline_objects

    def test_missing_properties_uses_defaults(self) -> None:
        tab = _build_tab({}, 2)
        assert tab.title == "Tab 3"
        assert tab.tab_id == "tab_2"
        assert tab.body == {}

    def test_real_fixture_tab(self) -> None:
        """Build tab from real multi-tab fixture data."""
        fixture = json.loads((FIXTURES_DIR / "docs" / "real_multi_tab.json").read_text())
        first_tab = fixture["tabs"][0]

        tab = _build_tab(first_tab, 0)
        assert tab.tab_id == "t.0"
        assert tab.title == "Sue"
        assert "content" in tab.body


class TestBuildLegacyTab:
    """Test legacy single-tab construction."""

    def test_basic_legacy(self) -> None:
        doc = {
            "title": "Old Doc",
            "body": {"content": []},
            "footnotes": {},
            "lists": {},
            "inlineObjects": {},
        }
        tab = _build_legacy_tab(doc)

        assert tab.title == "Old Doc"
        assert tab.tab_id == "main"
        assert tab.index == 0

    def test_missing_fields_use_defaults(self) -> None:
        tab = _build_legacy_tab({})
        assert tab.title == "Untitled"
        assert tab.body == {}


# ============================================================================
# FETCH DOCUMENT (mocked service, real fixture data)
# ============================================================================

class TestFetchDocument:
    """Test fetch_document with mocked Docs API."""

    @patch('adapters.docs.get_docs_service')
    def test_modern_multi_tab(self, mock_get_service) -> None:
        """Modern doc with tabs[] returns multi-tab DocData."""
        fixture = json.loads((FIXTURES_DIR / "docs" / "real_multi_tab.json").read_text())

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.documents().get().execute.return_value = fixture

        with patch('retry.time.sleep'):
            result = fetch_document("1iBsJHoqza53")

        assert isinstance(result, DocData)
        assert result.title == "Test multi-tab document"
        assert len(result.tabs) == len(fixture["tabs"])
        assert result.tabs[0].title == "Sue"

    @patch('adapters.docs.get_docs_service')
    def test_legacy_single_tab(self, mock_get_service) -> None:
        """Legacy doc without tabs[] creates single-tab DocData."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        # Legacy format: body at root level, no tabs[]
        mock_service.documents().get().execute.return_value = {
            "documentId": "legacy123",
            "title": "Legacy Document",
            "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Hello"}}]}}]},
        }

        with patch('retry.time.sleep'):
            result = fetch_document("legacy123")

        assert isinstance(result, DocData)
        assert result.title == "Legacy Document"
        assert len(result.tabs) == 1
        assert result.tabs[0].tab_id == "main"
        assert result.tabs[0].title == "Legacy Document"

    @patch('adapters.docs.get_docs_service')
    def test_untitled_document(self, mock_get_service) -> None:
        """Document without title gets 'Untitled' default."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.documents().get().execute.return_value = {
            "documentId": "notitle",
        }

        with patch('retry.time.sleep'):
            result = fetch_document("notitle")

        assert result.title == "Untitled"
        assert len(result.tabs) == 1  # Legacy fallback
