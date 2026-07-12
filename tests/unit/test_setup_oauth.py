"""Tests for tools/setup_oauth.py — the split OAuth bootstrap flow.

The flow's contract (mise-zefahe, mise-didage):
- The tool mints the auth URL exactly once; the subprocess only listens and
  exchanges (a second mint would overwrite the persisted PKCE verifier and
  orphan the returned URL).
- already_authenticated is only claimed when the credentials actually LOAD,
  not merely when a token blob exists.
"""

import socket
from unittest.mock import MagicMock, patch

import pytest

from oauth_config import port_is_free
from tools.setup_oauth import do_setup_oauth

FAKE_URL = (
    "https://accounts.google.com/o/oauth2/auth"
    "?state=st123&code_challenge=ch456&code_challenge_method=S256"
)


@pytest.fixture
def tmp_token_file(tmp_path, monkeypatch):
    """Point the module's TOKEN_FILE at a temp dir so log writes land there."""
    token_file = tmp_path / "token.json"
    monkeypatch.setattr("tools.setup_oauth.TOKEN_FILE", token_file)
    return token_file


class TestCredsValidityGate:
    """already_authenticated requires creds that load, not just a token blob."""

    def test_stale_creds_fall_through_to_fresh_flow(self, tmp_token_file):
        """Token present but unloadable → fresh flow, not already_authenticated.

        The old code swallowed the load failure and claimed already_authenticated,
        sending the user into an 'authed!' → 'auth failed' loop (mise-didage).
        """
        with (
            patch("tools.setup_oauth.has_token", return_value=True),
            patch(
                "adapters.http_client.get_sync_client",
                side_effect=FileNotFoundError(
                    "OAuth token is expired and refresh failed "
                    "(refresh_token may be revoked)."
                ),
            ),
            patch("tools.setup_oauth.port_is_free", return_value=True),
            patch("tools.setup_oauth.get_auth_url", return_value=FAKE_URL),
            patch("tools.setup_oauth.subprocess.Popen") as popen,
        ):
            result = do_setup_oauth(force=False)

        assert result["status"] == "reauthenticating_stale_creds"
        assert result["url"] == FAKE_URL
        assert "refresh failed" in result["cues"]["stale_creds_diagnostic"]
        popen.assert_called_once()

    def test_valid_creds_return_already_authenticated(self, tmp_token_file):
        """Token present and loads cleanly → already_authenticated, no spawn."""
        with (
            patch("tools.setup_oauth.has_token", return_value=True),
            patch("adapters.http_client.get_sync_client", return_value=MagicMock()),
            patch("tools.setup_oauth.subprocess.Popen") as popen,
        ):
            result = do_setup_oauth(force=False)

        assert result["status"] == "already_authenticated"
        popen.assert_not_called()

    def test_force_skips_validity_check_entirely(self, tmp_token_file):
        """force=true goes straight to a fresh flow without probing the token."""
        with (
            patch("tools.setup_oauth.has_token", return_value=True) as has_tok,
            patch("adapters.http_client.get_sync_client") as sync_client,
            patch("tools.setup_oauth.port_is_free", return_value=True),
            patch("tools.setup_oauth.get_auth_url", return_value=FAKE_URL),
            patch("tools.setup_oauth.can_open_browser", return_value=True),
            patch("tools.setup_oauth.subprocess.Popen") as popen,
        ):
            result = do_setup_oauth(force=True)

        assert result["status"] == "browser_opening"
        assert "stale_creds_diagnostic" not in result["cues"]
        sync_client.assert_not_called()
        popen.assert_called_once()


class TestEarlyReturns:
    """Error branches before any flow is spawned."""

    def test_missing_credentials_json(self, tmp_token_file, tmp_path):
        with patch(
            "tools.setup_oauth.LOCAL_CREDENTIALS_FILE", tmp_path / "nope.json"
        ):
            result = do_setup_oauth()

        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "Reinstall the mise plugin" in result["message"]

    def test_port_busy_returns_network_error(self, tmp_token_file):
        with (
            patch("tools.setup_oauth.has_token", return_value=False),
            patch("tools.setup_oauth.port_is_free", return_value=False),
            patch("tools.setup_oauth.subprocess.Popen") as popen,
        ):
            result = do_setup_oauth()

        assert result["error"] is True
        assert result["kind"] == "network_error"
        assert "already in use" in result["message"]
        popen.assert_not_called()


class TestSingleMintInvariant:
    """The tool mints ONE URL; the subprocess must consume it, never re-mint.

    A second mint overwrites the persisted PKCE verifier, orphaning the
    returned URL (mise-zefahe — challenge A vs verifier B, exchange fails).
    """

    def test_subprocess_receives_the_returned_url(self, tmp_token_file):
        """The spawned command carries --url with the exact URL we return."""
        with (
            patch("tools.setup_oauth.has_token", return_value=False),
            patch("tools.setup_oauth.port_is_free", return_value=True),
            patch("tools.setup_oauth.get_auth_url", return_value=FAKE_URL),
            patch("tools.setup_oauth.can_open_browser", return_value=True),
            patch("tools.setup_oauth.subprocess.Popen") as popen,
        ):
            result = do_setup_oauth()

        argv = popen.call_args.args[0]
        assert "--auto" in argv
        assert "--url" in argv
        assert argv[argv.index("--url") + 1] == result["url"] == FAKE_URL
        # Response shape for the calling Claude
        assert result["status"] == "browser_opening"
        for key in ("fallback", "log_path", "token_will_save_to"):
            assert key in result["cues"]

    def test_returned_url_challenge_matches_persisted_verifier(self, tmp_token_file):
        """End-to-end property through REAL jeton URL minting (no network):
        the returned URL's code_challenge must be the S256 hash of the
        verifier persisted next to TOKEN_FILE — i.e. the URL is exchangeable.
        """
        import base64
        import hashlib
        import json
        from urllib.parse import parse_qs, urlparse

        with (
            patch("tools.setup_oauth.has_token", return_value=False),
            patch("tools.setup_oauth.port_is_free", return_value=True),
            patch("tools.setup_oauth.subprocess.Popen"),
        ):
            result = do_setup_oauth()

        challenge = parse_qs(urlparse(result["url"]).query)["code_challenge"][0]
        pkce_state = json.loads(
            (tmp_token_file.parent / ".pkce_state.json").read_text()
        )
        derived = (
            base64.urlsafe_b64encode(
                hashlib.sha256(pkce_state["code_verifier"].encode()).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        assert challenge == derived


class TestBrowserEnvStatus:
    """status/message must tell the truth about THIS environment (mise-petaga).

    The old code returned status='browser_opening' unconditionally — a lie on a
    headless box, where the spawned subprocess correctly logs 'not opening a
    browser'. The tool can predict the subprocess's decision because the child
    inherits its env, so it reads can_open_browser() and reports honestly.
    """

    def _fresh_flow(self, browser: bool, tmp_token_file):
        with (
            patch("tools.setup_oauth.has_token", return_value=False),
            patch("tools.setup_oauth.port_is_free", return_value=True),
            patch("tools.setup_oauth.get_auth_url", return_value=FAKE_URL),
            patch("tools.setup_oauth.can_open_browser", return_value=browser),
            patch("tools.setup_oauth.subprocess.Popen"),
        ):
            return do_setup_oauth()

    def test_headless_status_and_url_led_message(self, tmp_token_file):
        result = self._fresh_flow(browser=False, tmp_token_file=tmp_token_file)
        assert result["status"] == "headless_use_url"
        assert result["url"] == FAKE_URL
        # Never the browser branch's false promise; leads with URL + tunnel/--code.
        assert "should be opening" not in result["message"].lower()
        assert "headless" in result["message"].lower()
        assert "ssh -L 3000:localhost:3000" in result["message"]
        assert "--code" in result["message"]

    def test_browser_env_status_and_message(self, tmp_token_file):
        result = self._fresh_flow(browser=True, tmp_token_file=tmp_token_file)
        assert result["status"] == "browser_opening"
        assert "browser tab should be opening" in result["message"].lower()

    def test_stale_status_wins_but_message_keeps_env_truth(self, tmp_token_file):
        """A stale re-auth on a headless box: status names the re-auth, but the
        message still carries the headless URL/tunnel guidance (not a browser)."""
        with (
            patch("tools.setup_oauth.has_token", return_value=True),
            patch(
                "adapters.http_client.get_sync_client",
                side_effect=FileNotFoundError("refresh failed"),
            ),
            patch("tools.setup_oauth.port_is_free", return_value=True),
            patch("tools.setup_oauth.get_auth_url", return_value=FAKE_URL),
            patch("tools.setup_oauth.can_open_browser", return_value=False),
            patch("tools.setup_oauth.subprocess.Popen"),
        ):
            result = do_setup_oauth()
        assert result["status"] == "reauthenticating_stale_creds"
        assert "ssh -L 3000:localhost:3000" in result["message"]


class TestPreMintedCallbackHandler:
    """The listener's state (CSRF) validation — auth.py's handler."""

    @staticmethod
    def _poke(path: str) -> tuple[int, object]:
        """Run one request against a throwaway handler server, return
        (http_status, server.oauth_result).

        Uses plain HTTPServer (not the production ThreadingHTTPServer) so
        handle_request() completes the handler synchronously — do_GET's
        logic is identical under either mixin, and this removes the
        read-before-handler-finishes race from the test itself.
        """
        import http.client
        import threading
        from http.server import HTTPServer

        from auth import _PreMintedCallbackHandler

        server = HTTPServer(("localhost", 0), _PreMintedCallbackHandler)
        server.oauth_result = None
        server.expected_state = "goodstate"
        server.timeout = 5
        port = server.server_address[1]

        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        conn = http.client.HTTPConnection("localhost", port, timeout=5)
        conn.request("GET", path)
        status = conn.getresponse().status
        conn.close()
        thread.join(timeout=5)
        server.server_close()
        return status, server.oauth_result

    def test_good_state_yields_code(self):
        status, result = self._poke("/oauth/callback?code=abc123&state=goodstate")
        assert status == 200
        assert result == ("code", "abc123")

    def test_state_mismatch_rejected(self):
        status, result = self._poke("/oauth/callback?code=abc123&state=EVIL")
        assert status == 400
        assert result == ("error", "state_mismatch")

    def test_provider_error_surfaced(self):
        status, result = self._poke("/oauth/callback?error=access_denied")
        assert status == 400
        assert result == ("error", "access_denied")

    def test_unrelated_path_is_404_and_keeps_listening(self):
        status, result = self._poke("/favicon.ico")
        assert status == 404
        assert result is None  # not consumed — the flow keeps waiting


class TestAuthCli:
    """auth.py argument contract."""

    def test_url_requires_auto(self):
        import subprocess
        import sys

        r = subprocess.run(
            [sys.executable, "-m", "auth", "--url", "https://example.com"],
            capture_output=True,
            text=True,
            cwd=str(__import__("pathlib").Path(__file__).parents[2]),
        )
        assert r.returncode == 2
        assert "--url requires --auto" in r.stderr


class TestServerSeam:
    """force must survive the MCP surface: server.do → dispatch → handler.

    The 1.3.1 smoke test found force=true silently dropped at this seam —
    do()'s signature had no force param, so FastMCP's schema never declared
    it and pydantic discarded it, while the tool's own error message was
    recommending it. Unit tests one layer down stayed green (wrong-layer
    green). This pins the full path.
    """

    def test_force_is_in_do_signature(self):
        """FastMCP generates the tool schema from the signature — the param
        must exist there or callers' force is dropped before dispatch."""
        import inspect

        from server import do

        assert "force" in inspect.signature(do).parameters

    def test_force_reaches_handler_through_server_do(self, tmp_token_file):
        with (
            patch("tools.setup_oauth.has_token", return_value=True),
            patch("adapters.http_client.get_sync_client") as sync_client,
            patch("tools.setup_oauth.port_is_free", return_value=True),
            patch("tools.setup_oauth.get_auth_url", return_value=FAKE_URL),
            patch("tools.setup_oauth.can_open_browser", return_value=True),
            patch("tools.setup_oauth.subprocess.Popen") as popen,
        ):
            from server import do

            result = do(operation="setup_oauth", force=True)

        assert result["status"] == "browser_opening"  # NOT already_authenticated
        popen.assert_called_once()
        sync_client.assert_not_called()


class TestPortIsFree:
    """port_is_free (oauth_config) — shared by the MCP tool and auth.py CLI."""

    def test_held_port_reports_busy(self):
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            holder.bind(("localhost", 0))
            holder.listen(1)
            port = holder.getsockname()[1]
            assert port_is_free(port) is False
        finally:
            holder.close()

    def test_released_port_reports_free(self):
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.bind(("localhost", 0))
        port = holder.getsockname()[1]
        holder.close()
        assert port_is_free(port) is True
