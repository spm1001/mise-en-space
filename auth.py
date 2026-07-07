#!/usr/bin/env python3
"""
OAuth Authentication for mise-en-space.

Two credential sources, tried in order:
1. Local credentials.json in repo root (for external users)
2. GCP Secret Manager (for maintainer — requires gcloud CLI)

Usage:
    uv run python -m auth                    # Auto mode (opens browser, or prints URL if headless)
    uv run python -m auth --code URL         # Exchange code from headless flow
    uv run python -m auth --project OTHER    # Use different GCP project

Prerequisites (external users):
    - credentials.json in repo root (from GCP Console)

Prerequisites (maintainer):
    - gcloud CLI installed and authenticated
    - Access to the GCP project containing the secret
"""

import os
import subprocess
import sys
import tempfile
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jeton import authenticate, get_auth_url

from oauth_config import (
    TOKEN_FILE,
    SCOPES,
    OAUTH_PORT,
    GCP_PROJECT,
    SECRET_NAME,
    LOCAL_CREDENTIALS_FILE,
    port_is_free,
)
from token_store import save_token

# How long the pre-minted-URL listener waits for the OAuth callback.
_LISTEN_TIMEOUT_S = 300


def fetch_credentials_from_secret_manager(project: str, secret_name: str) -> str:
    """Fetch OAuth client credentials from GCP Secret Manager."""
    cmd = [
        'gcloud', 'secrets', 'versions', 'access', 'latest',
        f'--secret={secret_name}',
        f'--project={project}',
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except FileNotFoundError:
        print("Error: gcloud CLI not found")
        print("Install: https://cloud.google.com/sdk/docs/install")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"Error fetching secret: {e.stderr.strip()}")
        print()
        print("Check that you have access to the secret:")
        print(f"  gcloud secrets versions access latest --secret={secret_name} --project={project}")
        sys.exit(1)


def _can_open_browser() -> bool:
    """Check if a graphical environment is available (mirrors jeton's check)."""
    if sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _print_code_instructions() -> None:
    print()
    print("After approving, the browser lands on a localhost URL (it may show a")
    print("connection error — that's fine, the code is in the address bar).")
    print("Copy that full URL and run:")
    print()
    print("  uv run python -m auth --code '<redirect_url>'")
    print()


class _PreMintedCallbackHandler(BaseHTTPRequestHandler):
    """OAuth callback handler for the pre-minted-URL listener.

    Reads `expected_state` from the server and writes the outcome to
    `server.oauth_result` as ("code", value) or ("error", reason).
    Module-level (not a closure) so the CSRF/state logic is unit-testable.
    """

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _respond(self, status: int, body: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        colour = "#2e7d32" if status == 200 else "#c62828"
        self.wfile.write(
            f'<!DOCTYPE html><html><body style="font-family:system-ui;'
            f'text-align:center;padding:60px"><h1 style="color:{colour}">'
            f"{body}</h1></body></html>".encode()
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/oauth/callback":
            self.send_response(404)
            self.end_headers()
            return
        expected_state = getattr(self.server, "expected_state", None)
        params = parse_qs(parsed.query)
        error = params.get("error", [None])[0]
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        if error:
            self._respond(400, f"Authorization failed: {error}")
            self.server.oauth_result = ("error", error)  # type: ignore[attr-defined]
        elif expected_state is not None and state != expected_state:
            self._respond(400, "State mismatch — stale or foreign callback. Restart authentication.")
            self.server.oauth_result = ("error", "state_mismatch")  # type: ignore[attr-defined]
        elif not code:
            self._respond(400, "No authorization code received")
            self.server.oauth_result = ("error", "no_code")  # type: ignore[attr-defined]
        else:
            self._respond(200, "&#10003; Authorization Successful — you can close this tab")
            self.server.oauth_result = ("code", code)  # type: ignore[attr-defined]


def _serve_pre_minted(auth_url: str, credentials_path: str) -> None:
    """Listener half of the split flow (setup_oauth spawns us in this mode).

    The MCP tool already minted auth_url via get_auth_url(), which persisted
    the PKCE verifier next to TOKEN_FILE. We must NOT mint a second URL —
    that would overwrite the verifier and orphan the URL the user is already
    clicking (mise-zefahe: one consent round-trip burned exactly this way).
    We only open the browser (best-effort), catch the localhost callback,
    and exchange the code — jeton loads the persisted verifier for that.

    Runs on headless boxes too: an SSH tunnel (ssh -L 3000:localhost:3000)
    can deliver the callback here. On timeout the verifier stays on disk,
    so the --code path keeps working.

    Threaded server: a single-threaded listener wedges when Chrome opens a
    speculative second connection that never sends a request (seen live).
    """
    expected_state = parse_qs(urlparse(auth_url).query).get("state", [None])[0]

    # Bind BEFORE opening the browser — a busy port must not burn a consent click.
    try:
        server = ThreadingHTTPServer(("localhost", OAUTH_PORT), _PreMintedCallbackHandler)
    except OSError as e:
        print(f"Cannot bind localhost:{OAUTH_PORT} ({e}) — another listener holds the port.")
        _print_code_instructions()
        sys.exit(1)

    if _can_open_browser():
        try:
            webbrowser.open(auth_url)
            print("Browser opened at Google's consent screen")
        except Exception:
            print("Could not auto-open browser — use the URL setup_oauth returned")
    else:
        print("Headless environment — not opening a browser.")
        print("Open the URL setup_oauth returned in any browser. The callback must")
        print(f"reach localhost:{OAUTH_PORT} on THIS machine: either run")
        print(f"  ssh -L {OAUTH_PORT}:localhost:{OAUTH_PORT} <this-host>")
        print("before clicking, or use the --code path when the redirect fails.")

    server.oauth_result = None  # type: ignore[attr-defined]
    server.expected_state = expected_state  # type: ignore[attr-defined]
    server.daemon_threads = True
    server.timeout = 1.0  # so the accept-loop wakes to check deadline/result
    deadline = time.monotonic() + _LISTEN_TIMEOUT_S

    print(f"Listening on http://localhost:{OAUTH_PORT}/oauth/callback "
          f"(up to {_LISTEN_TIMEOUT_S // 60} minutes)")
    while server.oauth_result is None and time.monotonic() < deadline:  # type: ignore[attr-defined]
        server.handle_request()
    server.server_close()

    result = server.oauth_result  # type: ignore[attr-defined]
    if result is None:
        print("Timed out waiting for the OAuth callback.")
        print("Your consent click is not wasted — the PKCE verifier is still saved.")
        _print_code_instructions()
        sys.exit(1)

    kind, value = result
    if kind == "error":
        print(f"OAuth callback reported an error: {value}")
        sys.exit(1)

    print("Authorization code received")
    authenticate(
        credentials_path=credentials_path,
        token_path=TOKEN_FILE,
        scopes=SCOPES,
        code=value,
        port=OAUTH_PORT,
    )
    save_token(TOKEN_FILE)
    print()
    print("Authentication complete.")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="OAuth authentication for mise-en-space"
    )
    parser.add_argument(
        '--code',
        type=str,
        help='Auth code or redirect URL (from headless flow)'
    )
    parser.add_argument(
        '--auto',
        action='store_true',
        help='Auto flow: open browser + run localhost callback listener + save token. Use this when running on a machine with a browser.'
    )
    parser.add_argument(
        '--url',
        type=str,
        help='Pre-minted auth URL (from setup_oauth). With --auto: listen and '
             'exchange WITHOUT generating a new URL — a second mint would '
             'overwrite the persisted PKCE verifier and orphan this URL.'
    )
    parser.add_argument(
        '--project',
        type=str,
        default=GCP_PROJECT,
        help=f'GCP project containing credentials secret (default: {GCP_PROJECT})'
    )

    args = parser.parse_args()

    if args.url and not args.auto:
        parser.error("--url requires --auto")

    # Resolve credentials: local file first, then Secret Manager
    tmp_path = None
    if LOCAL_CREDENTIALS_FILE.exists():
        print(f"Using local credentials: {LOCAL_CREDENTIALS_FILE}")
        credentials_path = str(LOCAL_CREDENTIALS_FILE)
    else:
        print(f"No local credentials.json found.")
        print(f"Fetching from Secret Manager: {args.project}/{SECRET_NAME}...")
        credentials_json = fetch_credentials_from_secret_manager(args.project, SECRET_NAME)
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        tmp.write(credentials_json)
        tmp.close()
        tmp_path = tmp.name
        credentials_path = tmp_path

    try:
        if args.code:
            creds = authenticate(
                credentials_path=credentials_path,
                token_path=TOKEN_FILE,
                scopes=SCOPES,
                code=args.code,
                port=OAUTH_PORT,
            )
            save_token(TOKEN_FILE)
            print()
            print("Authentication complete.")
        elif args.auto and args.url:
            # Split flow: setup_oauth minted the URL (and owns the persisted
            # PKCE verifier); we only listen and exchange. Never mint here.
            _serve_pre_minted(args.url, credentials_path)
        elif args.auto:
            # Pre-check the callback port BEFORE jeton mints a URL and opens
            # the browser — failing after the consent screen burns a click
            # (parity with the MCP tool's pre-flight; mise-zanezo).
            if not port_is_free(OAUTH_PORT):
                print(f"Port {OAUTH_PORT} on localhost is already in use — the OAuth")
                print("callback listener needs it. It may be an earlier auth listener")
                print("still in its window, or another dev server. Free the port and")
                print("retry, or use the two-step flow:")
                print()
                print("  uv run python -m auth          # prints the URL")
                print("  uv run python -m auth --code '<redirect_url>'")
                sys.exit(1)
            # Auto flow: jeton.authenticate() opens browser, listens on localhost,
            # exchanges code, writes token to TOKEN_FILE. We then move it to Keychain.
            authenticate(
                credentials_path=credentials_path,
                token_path=TOKEN_FILE,
                scopes=SCOPES,
                port=OAUTH_PORT,
            )
            save_token(TOKEN_FILE)
            print()
            print("Authentication complete.")
        else:
            url = get_auth_url(
                credentials_path=credentials_path,
                token_path=TOKEN_FILE,
                scopes=SCOPES,
                port=OAUTH_PORT,
            )
            print()
            print("Open this URL in your browser:")
            print()
            print(url)
            print()
            print("After granting permissions, copy the redirect URL and run:")
            print()
            print(f"  uv run python -m auth --code '<redirect_url>'")
            print()
    except KeyboardInterrupt:
        print("\n\nAuthentication cancelled")
        sys.exit(1)
    except Exception as e:
        print(f"\nAuthentication failed: {e}")
        sys.exit(1)
    finally:
        # Clean up temp credentials file (only if we fetched from Secret Manager)
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
