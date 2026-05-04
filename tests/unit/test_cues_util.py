"""Tests for cues_util — identity self-disclosure helpers."""

import json
from unittest.mock import MagicMock, patch

import pytest

import cues_util
from cues_util import (
    clear_user_email_cache,
    current_user_email,
    resolve_user_email_eager,
    with_identity,
)


@pytest.fixture
def fresh_cache():
    """Clear identity cache before and after each test."""
    clear_user_email_cache()
    yield
    clear_user_email_cache()


# =============================================================================
# with_identity
# =============================================================================


class TestWithIdentity:
    def test_returns_copy_when_unresolved(self, fresh_cache):
        """Unresolved identity → return cues unchanged (as a copy)."""
        original = {"action": "Created"}
        result = with_identity(original)
        assert result == {"action": "Created"}
        assert result is not original  # new dict

    def test_injects_identity_when_resolved(self, fresh_cache):
        """Resolved identity → injects _identity field."""
        with patch("cues_util.current_user_email", return_value="user@itv.com"):
            result = with_identity({"action": "Created"})
        assert result == {
            "action": "Created",
            "_identity": {"email": "user@itv.com"},
        }

    def test_does_not_mutate_input(self, fresh_cache):
        """Source cues dict is not modified."""
        original = {"foo": 1}
        with patch("cues_util.current_user_email", return_value="user@itv.com"):
            with_identity(original)
        assert "_identity" not in original


# =============================================================================
# resolve_user_email_eager
# =============================================================================


class TestResolveUserEmailEager:
    def test_reads_from_token_file_when_identity_present(self, fresh_cache, tmp_path):
        """Cheap path: _identity already in token → no HTTP call."""
        token = {
            "token": "ya29.x",
            "_identity": {"email": "cached@itv.com"},
        }
        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps(token))
        client = MagicMock()

        resolve_user_email_eager(client, token_file)

        assert current_user_email() == "cached@itv.com"
        client.get_json.assert_not_called()

    @patch("token_store.store_to_keychain", return_value=True)
    def test_backfills_via_drive_about_when_identity_missing(
        self, mock_store, fresh_cache, tmp_path
    ):
        """Legacy token: no _identity → call Drive about → write to Keychain."""
        token = {"token": "ya29.x", "refresh_token": "1//y"}
        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps(token))
        client = MagicMock()
        client.get_json.return_value = {"user": {"emailAddress": "fresh@itv.com"}}

        resolve_user_email_eager(client, token_file)

        client.get_json.assert_called_once()
        url = client.get_json.call_args[0][0]
        assert "drive/v3/about" in url
        assert current_user_email() == "fresh@itv.com"
        # Keychain written with enriched token
        mock_store.assert_called_once()
        enriched = json.loads(mock_store.call_args[0][0])
        assert enriched["_identity"] == {"email": "fresh@itv.com"}

    def test_missing_token_file_leaves_identity_unresolved(self, fresh_cache, tmp_path):
        """No token file → cache stays None, no exception."""
        client = MagicMock()
        resolve_user_email_eager(client, tmp_path / "missing.json")
        assert current_user_email() is None

    def test_idempotent_after_resolution(self, fresh_cache, tmp_path):
        """Second call is a no-op once resolved."""
        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps({
            "token": "ya29.x", "_identity": {"email": "first@itv.com"},
        }))
        client = MagicMock()
        resolve_user_email_eager(client, token_file)

        # Even if the file changes underneath, second call doesn't re-read
        token_file.write_text(json.dumps({
            "token": "ya29.x", "_identity": {"email": "different@itv.com"},
        }))
        resolve_user_email_eager(client, token_file)
        assert current_user_email() == "first@itv.com"

    def test_drive_about_failure_logs_and_caches_none(
        self, fresh_cache, tmp_path, caplog
    ):
        """HTTP failure → cache None, log a warning, don't raise."""
        token = {"token": "ya29.x"}
        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps(token))
        client = MagicMock()
        client.get_json.side_effect = RuntimeError("boom")

        resolve_user_email_eager(client, token_file)

        assert current_user_email() is None
        assert any(
            "Identity resolution failed" in rec.message
            for rec in caplog.records
        )


# =============================================================================
# Autouse-fixture meta-test
# =============================================================================


class TestAutoUseFixtureCorrectness:
    """Canary: the conftest autouse fixture actually patches the right path.

    Without this, all _identity tests across the suite could pass coincidentally
    if the patch path were wrong (typo, refactor, module rename) — they'd just
    inherit the developer's live Keychain identity silently.
    """

    def test_default_identity_is_none_in_tests(self):
        """The autouse fixture must keep current_user_email patched to None
        unless a test opts in by re-patching."""
        assert cues_util.current_user_email() is None
