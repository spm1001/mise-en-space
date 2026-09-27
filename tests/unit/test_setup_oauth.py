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

    @pytest.mark.parametrize("fault", ["transport", "token_endpoint_503"])
    def test_offline_is_not_stale_creds(self, tmp_token_file, fault):
        """Loading refreshes, so an offline check fails. That must come back
        as a network error, not as stale creds that mint a URL and hold the
        callback port for five minutes (jeton-vajiro)."""
        from google.auth.exceptions import RefreshError, TransportError

        exc = (TransportError("Connection reset by peer") if fault == "transport"
               else RefreshError("internal_failure", retryable=True))
        with (
            patch("tools.setup_oauth.has_token", return_value=True),
            patch("adapters.http_client.get_sync_client", side_effect=exc),
            patch("tools.setup_oauth.get_auth_url") as mint,
            patch("tools.setup_oauth.subprocess.Popen") as spawn,
        ):
            result = do_setup_oauth()
        assert result["kind"] == "network_error"
        assert "not an authentication" in result["message"]
        mint.assert_not_called()
        spawn.assert_not_called()

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

    def test_missing_credentials_json(self, tmp_token_file, tmp_path, monkeypatch):
        monkeypatch.delenv("MISE_EN_SPACE_OAUTH_CLIENT", raising=False)
        with patch("oauth_config.BUNDLED_CLIENT_FILE", tmp_path / "nope.json"):
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

        params = parse_qs(urlparse(result["url"]).query)
        challenge = params["code_challenge"][0]
        state = params["state"][0]
        pkce_state = json.loads(
            (tmp_token_file.parent / ".pkce_state.json").read_text()
        )
        # jeton 1.4.0 keys verifiers by the flow's state param (concurrent
        # flows merge instead of clobbering) — look up THIS URL's entry.
        verifier = pkce_state["flows"][state]["code_verifier"]
        derived = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
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


class TestPreMintedListener:
    """auth.py's listener half hands the pre-minted URL to jeton.

    jeton's authenticate(auth_url=...) owns the listener now — state (CSRF)
    check, bind before browser, threaded server, verifier kept on timeout —
    and pins those in its own suite (jeton-jedaza). What mise still owns: the
    URL goes through unminted, the browser decision is mise's, and each
    failure ends with the right next step.
    """

    URL = FAKE_URL + "&redirect_uri=http%3A%2F%2Flocalhost%3A3000%2Foauth%2Fcallback"

    def _run(self, open_browser=True, side_effect=None):
        import auth

        with (
            patch("auth._can_open_browser", return_value=open_browser),
            patch("auth.authenticate", side_effect=side_effect) as jeton_auth,
            patch("auth.get_auth_url") as mint,
            patch("auth.save_token") as save,
        ):
            auth._serve_pre_minted(self.URL, "/path/credentials.json")
        return jeton_auth, mint, save

    def test_url_goes_to_jeton_unminted(self):
        jeton_auth, mint, save = self._run(open_browser=True)
        mint.assert_not_called()  # a second mint would orphan the returned URL
        kwargs = jeton_auth.call_args.kwargs
        assert kwargs["auth_url"] == self.URL
        assert kwargs["open_browser"] is True
        assert "code" not in kwargs
        save.assert_called_once()

    def test_mise_browser_decision_reaches_jeton(self):
        """MISE_NO_BROWSER / XRDP say no: jeton must not open one either."""
        jeton_auth, _, _ = self._run(open_browser=False)
        assert jeton_auth.call_args.kwargs["open_browser"] is False

    def test_timeout_points_at_the_code_path(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self._run(side_effect=TimeoutError("no callback"))
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "not wasted" in out and "--code" in out

    def test_taken_port_points_at_the_code_path(self, capsys):
        import errno

        busy = OSError(errno.EADDRINUSE, "Cannot listen on localhost:3000")
        with pytest.raises(SystemExit) as exc:
            self._run(side_effect=busy)
        assert exc.value.code == 1
        assert "--code" in capsys.readouterr().out

    def test_other_os_errors_are_not_dressed_as_a_busy_port(self):
        with pytest.raises(FileNotFoundError):
            self._run(side_effect=FileNotFoundError(2, "credentials.json"))


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
