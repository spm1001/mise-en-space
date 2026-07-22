"""
Tests for docs adapter using mocked HTTP client and real fixtures.

Mocks the sync HTTP client, feeds real fixture data,
and verifies the adapter parses into DocData correctly.
"""

import pytest
import orjson
from unittest.mock import patch, MagicMock

from models import DocData, MiseError
from adapters.docs import fetch_document, _build_tab, _build_legacy_tab
from tests.conftest import load_fixture


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
        fixture = load_fixture("docs", "real_multi_tab")
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
# FETCH DOCUMENT (mocked HTTP client, real fixture data)
# ============================================================================

class TestFetchDocument:
    """Test fetch_document with mocked sync HTTP client."""

    @patch('adapters.docs.get_sync_client')
    def test_modern_multi_tab(self, mock_get_client) -> None:
        """Modern doc with tabs[] returns multi-tab DocData."""
        fixture = load_fixture("docs", "real_multi_tab")

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = fixture

        with patch('retry.time.sleep'):
            result = fetch_document("1iBsJHoqza53")

        assert isinstance(result, DocData)
        assert result.title == "Test multi-tab document"
        assert len(result.tabs) == len(fixture["tabs"])
        assert result.tabs[0].title == "Sue"

        # Verify correct URL and params
        call_args = mock_client.get_json.call_args
        assert "1iBsJHoqza53" in call_args.args[0]
        assert call_args.kwargs["params"]["includeTabsContent"] == "true"

    @patch('adapters.docs.get_sync_client')
    def test_legacy_single_tab(self, mock_get_client) -> None:
        """Legacy doc without tabs[] creates single-tab DocData."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_json.return_value = {
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

    @patch('adapters.docs.get_sync_client')
    def test_untitled_document(self, mock_get_client) -> None:
        """Document without title gets 'Untitled' default."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {"documentId": "notitle"}

        with patch('retry.time.sleep'):
            result = fetch_document("notitle")

        assert result.title == "Untitled"
        assert len(result.tabs) == 1  # Legacy fallback


# ============================================================================
# SUGGESTED EDITS — the mode dance (mise-wofomu)
# ============================================================================

def _inline_view_doc() -> dict:
    """Legacy-shape doc as SUGGESTIONS_INLINE returns it: suggestion runs tagged."""
    return {
        "documentId": "sugg123",
        "title": "Firework",
        "body": {"content": [
            {"paragraph": {"elements": [
                {"textRun": {"content": "Six feet under screams, "}},
                {"textRun": {"content": "but no one cares about this song",
                             "suggestedInsertionIds": ["suggest.abc"]}},
                {"textRun": {"content": "but no one seems to hear a thing\n",
                             "suggestedDeletionIds": ["suggest.abc"]}},
            ]}},
            {"paragraph": {"elements": [
                {"textRun": {"content": "'Cause there's a spark in you\n",
                             "suggestedDeletionIds": ["suggest.def"]}},
            ]}},
        ]},
    }


def _accepted_view_doc() -> dict:
    """The same doc as PREVIEW_SUGGESTIONS_ACCEPTED returns it: resolved."""
    return {
        "documentId": "sugg123",
        "title": "Firework",
        "body": {"content": [
            {"paragraph": {"elements": [
                {"textRun": {"content": "Six feet under screams, but no one cares about this song\n"}},
            ]}},
        ]},
    }


class TestFetchDocumentSuggestions:
    """fetch_document's suggestions= mode dance."""

    @patch('adapters.docs.get_sync_client')
    def test_clean_doc_is_single_call(self, mock_get_client) -> None:
        """No suggestions → one API call, inline view requested, count 0."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "documentId": "clean1", "title": "Clean",
            "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Hello\n"}}]}}]},
        }

        with patch('retry.time.sleep'):
            result = fetch_document("clean1")

        assert mock_client.get_json.call_count == 1
        params = mock_client.get_json.call_args.kwargs["params"]
        assert params["suggestionsViewMode"] == "SUGGESTIONS_INLINE"
        assert result.suggestion_count == 0
        assert result.adapter_warnings == []

    @patch('adapters.docs.get_sync_client')
    def test_accepted_mode_second_call(self, mock_get_client) -> None:
        """Suggestions present + accepted (default) → second call, resolved server-side."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.side_effect = [_inline_view_doc(), _accepted_view_doc()]

        with patch('retry.time.sleep'):
            result = fetch_document("sugg123")

        assert mock_client.get_json.call_count == 2
        second_params = mock_client.get_json.call_args_list[1].kwargs["params"]
        assert second_params["suggestionsViewMode"] == "PREVIEW_SUGGESTIONS_ACCEPTED"
        assert result.suggestion_count == 2
        assert result.suggestions_mode == "accepted"
        assert any("ACCEPTED" in w for w in result.adapter_warnings)
        # Content is the resolved body, not the inline mash
        run = result.tabs[0].body["content"][0]["paragraph"]["elements"][0]["textRun"]
        assert run["content"] == "Six feet under screams, but no one cares about this song\n"

    @patch('adapters.docs.get_sync_client')
    def test_original_mode_second_call(self, mock_get_client) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.side_effect = [_inline_view_doc(), _accepted_view_doc()]

        with patch('retry.time.sleep'):
            result = fetch_document("sugg123", suggestions="original")

        second_params = mock_client.get_json.call_args_list[1].kwargs["params"]
        assert second_params["suggestionsViewMode"] == "PREVIEW_WITHOUT_SUGGESTIONS"
        assert any("ORIGINAL" in w for w in result.adapter_warnings)

    @patch('adapters.docs.get_sync_client')
    def test_markup_mode_single_call_annotates(self, mock_get_client) -> None:
        """Markup mode keeps the inline body (no second call) and tags runs."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = _inline_view_doc()

        with patch('retry.time.sleep'):
            result = fetch_document("sugg123", suggestions="markup")

        assert mock_client.get_json.call_count == 1
        assert result.suggestion_count == 2
        assert result.suggestions_mode == "markup"
        assert any("markup" in w for w in result.adapter_warnings)
        tagged = [
            e["textRun"].get("_mise_suggestion_kind")
            for p in result.tabs[0].body["content"]
            for e in p["paragraph"]["elements"]
            if "textRun" in e and e["textRun"].get("_mise_suggestion_kind")
        ]
        assert tagged == ["ins", "del", "del"]

    @patch('adapters.docs.get_sync_client')
    def test_invalid_mode_raises(self, mock_get_client) -> None:
        """Defensive check fires before any API call (surfaces as MiseError via @with_retry)."""
        with pytest.raises(MiseError, match="suggestions must be one of"):
            with patch('retry.time.sleep'):
                fetch_document("any", suggestions="bogus")
        mock_get_client.return_value.get_json.assert_not_called()
