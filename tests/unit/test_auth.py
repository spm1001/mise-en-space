"""Tests for auth module — credential resolution."""

from pathlib import Path

import pytest

from oauth_config import LOCAL_CREDENTIALS_FILE


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
