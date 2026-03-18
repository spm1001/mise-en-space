"""Tests for auth module — browser detection and credential resolution."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from auth import _can_open_browser
from oauth_config import LOCAL_CREDENTIALS_FILE


# =============================================================================
# _can_open_browser()
# =============================================================================


class TestCanOpenBrowser:
    """Browser detection for auto vs manual OAuth mode."""

    def test_macos_always_true(self):
        """macOS has `open` command — always capable of opening a browser."""
        with patch.object(sys, "platform", "darwin"):
            with patch.dict("os.environ", {}, clear=True):
                assert _can_open_browser() is True

    def test_macos_true_regardless_of_display(self):
        """macOS doesn't need DISPLAY — it uses `open`, not xdg-open."""
        with patch.object(sys, "platform", "darwin"):
            with patch.dict("os.environ", {"DISPLAY": "", "WAYLAND_DISPLAY": ""}, clear=True):
                assert _can_open_browser() is True

    def test_linux_with_x11(self):
        """Linux with X11 display can open a browser."""
        with patch.object(sys, "platform", "linux"):
            with patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True):
                assert _can_open_browser() is True

    def test_linux_with_wayland(self):
        """Linux with Wayland display can open a browser."""
        with patch.object(sys, "platform", "linux"):
            with patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0"}, clear=True):
                assert _can_open_browser() is True

    def test_linux_no_display(self):
        """Linux without any display server — SSH, CI, headless."""
        with patch.object(sys, "platform", "linux"):
            with patch.dict("os.environ", {}, clear=True):
                assert _can_open_browser() is False

    def test_linux_empty_display(self):
        """Empty DISPLAY string is falsy — treat as no display."""
        with patch.object(sys, "platform", "linux"):
            with patch.dict("os.environ", {"DISPLAY": ""}, clear=True):
                assert _can_open_browser() is False


# =============================================================================
# Credential resolution
# =============================================================================


class TestCredentialResolution:
    """Bundled credentials.json should be found without Secret Manager."""

    def test_credentials_json_exists(self):
        """credentials.json ships with the repo — new users don't need gcloud."""
        assert LOCAL_CREDENTIALS_FILE.exists(), (
            "credentials.json missing from repo root. "
            "New users will fall through to Secret Manager and need gcloud CLI."
        )

    def test_credentials_json_is_valid(self):
        """credentials.json must be parseable JSON with expected structure."""
        import json

        data = json.loads(LOCAL_CREDENTIALS_FILE.read_text())
        # Google OAuth credentials have either "web" or "installed" key
        assert "web" in data or "installed" in data, (
            "credentials.json missing 'web' or 'installed' key"
        )

    def test_credentials_json_has_client_id(self):
        """Client ID must be present for OAuth flow."""
        import json

        data = json.loads(LOCAL_CREDENTIALS_FILE.read_text())
        cred_type = "web" if "web" in data else "installed"
        assert "client_id" in data[cred_type]
        assert data[cred_type]["client_id"], "client_id is empty"

    def test_credentials_json_has_redirect_uri(self):
        """Redirect URI must include localhost for the OAuth callback."""
        import json

        data = json.loads(LOCAL_CREDENTIALS_FILE.read_text())
        cred_type = "web" if "web" in data else "installed"
        redirect_uris = data[cred_type].get("redirect_uris", [])
        assert any("localhost" in uri for uri in redirect_uris), (
            "credentials.json missing localhost redirect URI"
        )
