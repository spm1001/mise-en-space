"""Tests for auth module — credential resolution and browser suitability."""

from pathlib import Path
from unittest.mock import patch

import pytest

from oauth_config import LOCAL_CREDENTIALS_FILE, can_open_browser


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


# =============================================================================
# can_open_browser — suitability gates (mise-zikesa)
# =============================================================================


class TestCanOpenBrowser:
    """A browser must be available AND suitable — a remote desktop's browser
    is often signed into the wrong Google account, so firing xdg-open at it
    burns the consent click on 'access blocked'."""

    def test_linux_with_display(self):
        with patch("oauth_config.sys") as mock_sys, \
                patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True):
            mock_sys.platform = "linux"
            assert can_open_browser() is True

    def test_linux_headless(self):
        with patch("oauth_config.sys") as mock_sys, \
                patch.dict("os.environ", {}, clear=True):
            mock_sys.platform = "linux"
            assert can_open_browser() is False

    def test_xrdp_session_suppresses_browser(self):
        """xrdp remote desktop: DISPLAY exists but the browser is the remote
        box's own — steer to --code instead."""
        with patch("oauth_config.sys") as mock_sys, \
                patch.dict(
                    "os.environ",
                    {"DISPLAY": ":10.0", "XRDP_SESSION": "1"},
                    clear=True,
                ):
            mock_sys.platform = "linux"
            assert can_open_browser() is False

    def test_mise_no_browser_override(self):
        """Explicit operator override — detection can't know account
        suitability, so a box can declare its browser unusable for OAuth."""
        with patch.dict(
            "os.environ", {"MISE_NO_BROWSER": "1", "DISPLAY": ":0"}, clear=True
        ):
            assert can_open_browser() is False

    def test_mise_no_browser_overrides_darwin(self):
        """The override wins even on macOS (always-True platform)."""
        with patch("oauth_config.sys") as mock_sys, \
                patch.dict(
                    "os.environ", {"MISE_NO_BROWSER": "1"}, clear=True
                ):
            mock_sys.platform = "darwin"
            assert can_open_browser() is False
