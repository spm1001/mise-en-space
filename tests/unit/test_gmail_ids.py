"""
Tests for adapters/gmail_ids.py — alternate Gmail identifiers → thread id.

get_thread_id_for_message predates the split and is covered through its
caller (test_id_diagnosis.py patches tools.fetch.gmail); these tests cover
the rfc822 resolver at the adapter layer with a mocked httpx client.
"""

from unittest.mock import patch, MagicMock

import pytest

from models import MiseError, ErrorKind
from adapters.gmail_ids import get_thread_id_for_draft, get_thread_id_for_rfc822_message_id


class TestGetThreadIdForRfc822MessageId:
    """rfc822msgid: search resolving a Message-ID to its thread (mise-lerulo)."""

    def _client_returning(self, payload):
        mock_client = MagicMock()
        mock_client.get_json.return_value = payload
        return mock_client

    @patch('adapters.gmail_ids.get_sync_client')
    def test_resolves_single_hit(self, mock_get_client) -> None:
        mock_get_client.return_value = self._client_returning(
            {"messages": [{"id": "aaaa000000000001", "threadId": "19fdaeed11138ef2"}]}
        )
        with patch('retry.time.sleep'):
            thread_id = get_thread_id_for_rfc822_message_id("abc=def@mail.gmail.com")
        assert thread_id == "19fdaeed11138ef2"

    @patch('adapters.gmail_ids.get_sync_client')
    def test_query_uses_rfc822msgid_operator(self, mock_get_client) -> None:
        client = self._client_returning(
            {"messages": [{"id": "a", "threadId": "b"}]}
        )
        mock_get_client.return_value = client
        with patch('retry.time.sleep'):
            get_thread_id_for_rfc822_message_id("abc@example.com")
        params = client.get_json.call_args.kwargs.get("params") or \
            client.get_json.call_args[0][1]
        assert params["q"] == "rfc822msgid:abc@example.com"

    @patch('adapters.gmail_ids.get_sync_client')
    def test_no_hit_raises_teaching_not_found(self, mock_get_client) -> None:
        # Gmail returns {} (no 'messages' key) for a query with zero hits
        mock_get_client.return_value = self._client_returning({})
        with patch('retry.time.sleep'):
            with pytest.raises(MiseError) as exc_info:
                get_thread_id_for_rfc822_message_id("missing@example.com")
        assert exc_info.value.kind is ErrorKind.NOT_FOUND
        assert "missing@example.com" in exc_info.value.message
        assert "Show original" in exc_info.value.message


class TestGetThreadIdForDraft:
    """drafts.get resolving a draft id to its holding thread (mise-jujoti step 7)."""

    def _client_returning(self, payload):
        mock_client = MagicMock()
        mock_client.get_json.return_value = payload
        return mock_client

    @patch('adapters.gmail_ids.get_sync_client')
    def test_resolves_thread_id(self, mock_get_client) -> None:
        mock_get_client.return_value = self._client_returning(
            {"message": {"threadId": "19fdaeed11138ef2"}}
        )
        with patch('retry.time.sleep'):
            thread_id = get_thread_id_for_draft("r8287431168042343092")
        assert thread_id == "19fdaeed11138ef2"

    @patch('adapters.gmail_ids.get_sync_client')
    def test_calls_drafts_get_fields_masked(self, mock_get_client) -> None:
        """The mask is the seam — one id is all this call exists to learn."""
        client = self._client_returning({"message": {"threadId": "t"}})
        mock_get_client.return_value = client
        with patch('retry.time.sleep'):
            get_thread_id_for_draft("r123")
        url = client.get_json.call_args[0][0]
        params = client.get_json.call_args.kwargs.get("params")
        assert url.endswith("/drafts/r123")
        assert params == {"fields": "message/threadId"}

    @patch('adapters.gmail_ids.get_sync_client')
    def test_empty_body_raises_not_found_naming_the_draft(self, mock_get_client) -> None:
        mock_get_client.return_value = self._client_returning({})
        with patch('retry.time.sleep'):
            with pytest.raises(MiseError) as exc_info:
                get_thread_id_for_draft("r999")
        assert exc_info.value.kind is ErrorKind.NOT_FOUND
        assert "r999" in exc_info.value.message
