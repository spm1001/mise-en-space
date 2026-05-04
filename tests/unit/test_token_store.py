"""Tests for token_store — Keychain-backed OAuth token persistence."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from token_store import (
    KEYCHAIN_SERVICE,
    get_from_keychain,
    store_to_keychain,
    delete_from_keychain,
    resolve_token_path,
    save_token,
    has_token,
)

# Sample token payload used across tests
SAMPLE_TOKEN = json.dumps({
    "access_token": "ya29.test",
    "refresh_token": "1//test-refresh",
    "client_id": "test-client.apps.googleusercontent.com",
    "token_uri": "https://oauth2.googleapis.com/token",
})

SAMPLE_TOKEN_HEX = SAMPLE_TOKEN.encode("utf-8").hex()


def _mock_security_success(stdout: str = "") -> MagicMock:
    """Return a CompletedProcess mimicking a successful `security` call."""
    result = MagicMock(spec=subprocess.CompletedProcess)
    result.stdout = stdout
    result.returncode = 0
    return result


# =============================================================================
# get_from_keychain()
# =============================================================================


class TestGetFromKeychain:
    """Reading tokens from macOS Keychain via `security find-generic-password`."""

    @patch("token_store.subprocess.run")
    @patch("token_store._has_keychain", return_value=True)
    def test_plain_json(self, _kc, mock_run):
        """Plain JSON returned by `security -w` is passed through unchanged."""
        mock_run.return_value = _mock_security_success(stdout=SAMPLE_TOKEN + "\n")
        result = get_from_keychain()
        assert result == SAMPLE_TOKEN
        parsed = json.loads(result)
        assert parsed["access_token"] == "ya29.test"

    @patch("token_store.subprocess.run")
    @patch("token_store._has_keychain", return_value=True)
    def test_hex_encoded_json(self, _kc, mock_run):
        """Long passwords are hex-encoded by `security` — decode transparently."""
        mock_run.return_value = _mock_security_success(stdout=SAMPLE_TOKEN_HEX + "\n")
        result = get_from_keychain()
        assert result == SAMPLE_TOKEN

    @patch("token_store.subprocess.run")
    @patch("token_store._has_keychain", return_value=True)
    def test_invalid_data_returns_none(self, _kc, mock_run):
        """Garbled data that isn't JSON or valid hex → None (don't raise)."""
        mock_run.return_value = _mock_security_success(stdout="not-json-not-hex\n")
        assert get_from_keychain() is None

    @patch("token_store._has_keychain", return_value=False)
    def test_no_keychain_returns_none(self, _kc):
        """Non-macOS or missing `security` binary → None without subprocess calls."""
        assert get_from_keychain() is None

    @patch("token_store.subprocess.run", side_effect=subprocess.CalledProcessError(44, "security"))
    @patch("token_store._has_keychain", return_value=True)
    def test_keychain_item_not_found(self, _kc, _run):
        """Missing Keychain entry (exit 44) → None, no exception propagated."""
        assert get_from_keychain() is None


# =============================================================================
# store_to_keychain()
# =============================================================================


class TestStoreToKeychain:
    """Writing tokens to macOS Keychain via `security add-generic-password`."""

    @patch("token_store.subprocess.run")
    @patch("token_store._has_keychain", return_value=True)
    def test_success(self, _kc, mock_run):
        """Delete-then-add sequence succeeds → True."""
        mock_run.return_value = _mock_security_success()
        assert store_to_keychain(SAMPLE_TOKEN) is True
        # First call: delete old entry; second call: add new entry
        assert mock_run.call_count == 2
        add_call = mock_run.call_args_list[1]
        assert "add-generic-password" in add_call[0][0]

    @patch("token_store.subprocess.run")
    @patch("token_store._has_keychain", return_value=True)
    def test_add_failure_returns_false(self, _kc, mock_run):
        """If `security add-generic-password` fails → False."""
        # Delete succeeds, add raises
        mock_run.side_effect = [
            _mock_security_success(),
            subprocess.CalledProcessError(1, "security"),
        ]
        assert store_to_keychain(SAMPLE_TOKEN) is False

    @patch("token_store._has_keychain", return_value=False)
    def test_no_keychain_returns_false(self, _kc):
        """Non-macOS → False without subprocess calls."""
        assert store_to_keychain(SAMPLE_TOKEN) is False


# =============================================================================
# delete_from_keychain()
# =============================================================================


class TestDeleteFromKeychain:
    """Removing tokens from macOS Keychain."""

    @patch("token_store.subprocess.run")
    @patch("token_store._has_keychain", return_value=True)
    def test_success(self, _kc, mock_run):
        mock_run.return_value = _mock_security_success()
        assert delete_from_keychain() is True

    @patch("token_store.subprocess.run", side_effect=subprocess.CalledProcessError(44, "security"))
    @patch("token_store._has_keychain", return_value=True)
    def test_not_found_returns_false(self, _kc, _run):
        assert delete_from_keychain() is False

    @patch("token_store._has_keychain", return_value=False)
    def test_no_keychain_returns_false(self, _kc):
        assert delete_from_keychain() is False


# =============================================================================
# resolve_token_path()
# =============================================================================


class TestResolveTokenPath:
    """Materializing a token file from Keychain for jeton.load_credentials()."""

    @patch("token_store.get_from_keychain", return_value=SAMPLE_TOKEN)
    def test_keychain_writes_to_fallback(self, _kc, tmp_path):
        """Keychain token is written to fallback_path so jeton can read it."""
        token_file = tmp_path / "token.json"
        result = resolve_token_path(token_file)
        assert result == token_file
        assert token_file.exists()
        assert json.loads(token_file.read_text())["access_token"] == "ya29.test"

    @patch("token_store._LEGACY_TOKEN_PATH", Path("/nonexistent/token.json"))
    @patch("token_store.get_from_keychain", return_value=None)
    def test_no_keychain_returns_fallback_path(self, _kc, tmp_path):
        """No Keychain entry, no legacy → return fallback_path as-is (may not exist)."""
        token_file = tmp_path / "token.json"
        result = resolve_token_path(token_file)
        assert result == token_file
        assert not token_file.exists()

    @patch("token_store.get_from_keychain", return_value=None)
    def test_existing_file_returned_without_keychain(self, _kc, tmp_path):
        """Pre-existing file on disk is returned when Keychain is empty."""
        token_file = tmp_path / "token.json"
        token_file.write_text(SAMPLE_TOKEN)
        result = resolve_token_path(token_file)
        assert result == token_file
        assert token_file.exists()

    @patch("token_store.get_from_keychain", return_value=None)
    def test_legacy_migration_copies_to_fallback(self, _kc, tmp_path):
        """Token at legacy path is copied to fallback (migration)."""
        legacy = tmp_path / "legacy" / "token.json"
        legacy.parent.mkdir()
        legacy.write_text(SAMPLE_TOKEN)

        stable = tmp_path / "data" / "token.json"

        with patch("token_store._LEGACY_TOKEN_PATH", legacy):
            result = resolve_token_path(stable)

        assert result == stable
        assert stable.exists()
        assert json.loads(stable.read_text())["access_token"] == "ya29.test"

    @patch("token_store.get_from_keychain", return_value=None)
    def test_legacy_not_used_when_fallback_exists(self, _kc, tmp_path):
        """Fallback path takes priority over legacy — no unnecessary migration."""
        legacy = tmp_path / "legacy" / "token.json"
        legacy.parent.mkdir()
        legacy.write_text('{"access_token": "old"}')

        stable = tmp_path / "data" / "token.json"
        stable.parent.mkdir()
        stable.write_text(SAMPLE_TOKEN)

        with patch("token_store._LEGACY_TOKEN_PATH", legacy):
            result = resolve_token_path(stable)

        assert result == stable
        # Stable content unchanged (not overwritten by legacy)
        assert json.loads(stable.read_text())["access_token"] == "ya29.test"


class TestHasTokenLegacy:
    """Tests for has_token() with legacy path."""

    @patch("token_store.get_from_keychain", return_value=None)
    def test_finds_legacy_token(self, _kc, tmp_path):
        """has_token returns True when token exists at legacy path."""
        legacy = tmp_path / "legacy" / "token.json"
        legacy.parent.mkdir()
        legacy.write_text(SAMPLE_TOKEN)

        missing = tmp_path / "data" / "token.json"

        with patch("token_store._LEGACY_TOKEN_PATH", legacy):
            assert has_token(missing) is True

    @patch("token_store.get_from_keychain", return_value=None)
    def test_legacy_same_as_fallback_no_double_count(self, _kc, tmp_path):
        """When legacy == fallback and file missing, returns False (not True from self-match)."""
        token_file = tmp_path / "token.json"
        with patch("token_store._LEGACY_TOKEN_PATH", token_file):
            assert has_token(token_file) is False


# =============================================================================
# save_token()
# =============================================================================


class TestSaveToken:
    """Persisting a freshly-written token.json into Keychain."""

    @patch("token_store.store_to_keychain", return_value=True)
    def test_stores_and_removes_file(self, mock_store, tmp_path):
        """Success: token pushed to Keychain, file deleted."""
        token_file = tmp_path / "token.json"
        token_file.write_text(SAMPLE_TOKEN)
        save_token(token_file)
        mock_store.assert_called_once_with(SAMPLE_TOKEN)
        assert not token_file.exists()

    @patch("token_store.store_to_keychain", return_value=False)
    def test_keychain_failure_keeps_file(self, mock_store, tmp_path):
        """Keychain failure: file stays on disk as fallback."""
        token_file = tmp_path / "token.json"
        token_file.write_text(SAMPLE_TOKEN)
        save_token(token_file)
        mock_store.assert_called_once()
        assert token_file.exists()

    @patch("token_store.store_to_keychain")
    def test_missing_file_is_noop(self, mock_store, tmp_path):
        """Non-existent token file → early return, no Keychain call."""
        token_file = tmp_path / "token.json"
        save_token(token_file)
        mock_store.assert_not_called()

    @patch("token_store._fetch_user_email", return_value="user@itv.com")
    @patch("token_store.store_to_keychain", return_value=True)
    def test_enriches_with_identity_before_storing(
        self, mock_store, mock_fetch, tmp_path
    ):
        """save_token resolves user email and writes _identity into the token
        before pushing to Keychain — so future processes get it for free."""
        token_file = tmp_path / "token.json"
        token_file.write_text(SAMPLE_TOKEN)
        save_token(token_file)
        mock_fetch.assert_called_once_with("ya29.test")
        # The string passed to Keychain should now have _identity merged in.
        stored_json = mock_store.call_args[0][0]
        stored = json.loads(stored_json)
        assert stored["_identity"] == {"email": "user@itv.com"}
        # Original fields preserved
        assert stored["access_token"] == "ya29.test"
        assert stored["refresh_token"] == "1//test-refresh"

    @patch("token_store._fetch_user_email", return_value=None)
    @patch("token_store.store_to_keychain", return_value=True)
    def test_enrichment_failure_does_not_block_save(
        self, mock_store, mock_fetch, tmp_path
    ):
        """If userinfo resolution fails (returns None), token still saves
        without _identity — enrichment is best-effort."""
        token_file = tmp_path / "token.json"
        token_file.write_text(SAMPLE_TOKEN)
        save_token(token_file)
        mock_fetch.assert_called_once()
        mock_store.assert_called_once()
        stored = json.loads(mock_store.call_args[0][0])
        assert "_identity" not in stored

    @patch("token_store._fetch_user_email")
    @patch("token_store.store_to_keychain", return_value=True)
    def test_skips_enrichment_when_identity_already_present(
        self, mock_store, mock_fetch, tmp_path
    ):
        """If _identity is already in the token (e.g. saved by a prior run),
        don't re-fetch — saves an HTTP call on token rotation."""
        token_with_identity = json.dumps({
            **json.loads(SAMPLE_TOKEN),
            "_identity": {"email": "existing@itv.com"},
        })
        token_file = tmp_path / "token.json"
        token_file.write_text(token_with_identity)
        save_token(token_file)
        mock_fetch.assert_not_called()
        stored = json.loads(mock_store.call_args[0][0])
        assert stored["_identity"] == {"email": "existing@itv.com"}


# =============================================================================
# has_token()
# =============================================================================


class TestHasToken:
    """Checking whether any token source is available."""

    @patch("token_store.get_from_keychain", return_value=SAMPLE_TOKEN)
    def test_keychain_present(self, _kc, tmp_path):
        """Keychain has a token → True regardless of file."""
        token_file = tmp_path / "token.json"
        assert has_token(token_file) is True

    @patch("token_store.get_from_keychain", return_value=None)
    def test_file_present(self, _kc, tmp_path):
        """No Keychain, but file exists → True."""
        token_file = tmp_path / "token.json"
        token_file.write_text(SAMPLE_TOKEN)
        assert has_token(token_file) is True

    @patch("token_store._LEGACY_TOKEN_PATH", Path("/nonexistent/token.json"))
    @patch("token_store.get_from_keychain", return_value=None)
    def test_neither_present(self, _kc, tmp_path):
        """No Keychain, no file, no legacy → False."""
        token_file = tmp_path / "token.json"
        assert has_token(token_file) is False
