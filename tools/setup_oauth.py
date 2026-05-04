"""
Setup OAuth — bootstrap a user's Google credentials from inside an MCP session.

The auth flow needs three things that don't normally happen during an MCP call:
- a Mac browser to open
- a localhost callback listener
- a wait of up to a few minutes for the user to click through

We solve this by spawning `python -m auth --auto` as a detached subprocess.
The subprocess opens the browser, listens on localhost:3000, exchanges the code,
and saves the token to Keychain via save_token(). The MCP tool returns
immediately with the auth URL inline as a fallback in case browser auto-open
fails (e.g. headless Mac).

User flow in Cowork:
1. Calls mise.do(operation="setup_oauth")
2. Browser tab opens (or they paste the URL we returned)
3. They approve at Google's consent screen
4. Subprocess saves token to Keychain
5. They retry their original mise call — now it works.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from jeton import get_auth_url

from oauth_config import LOCAL_CREDENTIALS_FILE, OAUTH_PORT, SCOPES, TOKEN_FILE
from token_store import has_token

# Where the subprocess lives — at the package root, runnable as `python -m auth`.
_PACKAGE_ROOT = Path(__file__).parent.parent


def do_setup_oauth(force: bool = False, **_kwargs: Any) -> dict[str, Any]:
    """
    Spawn the OAuth flow in a detached subprocess. Returns immediately.

    Args:
        force: If True, run setup even when a token already exists.
               Useful when the existing token is invalid but has_token() can't tell.

    Returns:
        dict with status + url (inline fallback) + cues for the calling Claude.
    """
    if not LOCAL_CREDENTIALS_FILE.exists():
        return {
            "error": True,
            "kind": "invalid_input",
            "message": (
                f"OAuth client config not found at {LOCAL_CREDENTIALS_FILE}. "
                "This shouldn't happen for a normal install — credentials.json "
                "ships with the plugin. Reinstall the mise plugin."
            ),
        }

    if has_token(TOKEN_FILE) and not force:
        from cues_util import with_identity
        # Trigger sync client init so identity is eager-resolved before we
        # build the response. Best-effort; if creds are corrupt, identity
        # stays None and the cue is silently omitted.
        try:
            from adapters.http_client import get_sync_client
            get_sync_client()
        except Exception:
            pass
        return {
            "operation": "setup_oauth",
            "status": "already_authenticated",
            "message": (
                "An OAuth token is already present. Try your original mise call again. "
                "If it fails with an auth error, call setup_oauth with force=true to re-auth."
            ),
            "cues": with_identity({
                "token_location": str(TOKEN_FILE),
            }),
        }

    # Pre-flight: is the OAuth callback port free?
    if not _port_is_free(OAUTH_PORT):
        return {
            "error": True,
            "kind": "network_error",
            "message": (
                f"Port {OAUTH_PORT} on localhost is already in use. "
                "OAuth needs this port for the callback listener. "
                "Stop whatever's using it (often a Node dev server) and try again."
            ),
        }

    # Generate the URL ourselves so we can return it inline.
    # The subprocess will generate its own URL too — that's fine, both are valid
    # but only the subprocess's listener will receive the callback.
    try:
        auth_url = get_auth_url(
            credentials_path=str(LOCAL_CREDENTIALS_FILE),
            token_path=TOKEN_FILE,
            scopes=SCOPES,
            port=OAUTH_PORT,
        )
    except Exception as e:
        return {
            "error": True,
            "kind": "unknown",
            "message": f"Failed to generate auth URL: {e}",
        }

    # Spawn the auth flow in a detached subprocess.
    # start_new_session=True so it survives mise restart;
    # stdin/out/err to DEVNULL so we don't hold pipes open.
    log_path = TOKEN_FILE.parent / "setup_oauth.log"
    try:
        log_fh = open(log_path, "w")
        subprocess.Popen(
            [sys.executable, "-m", "auth", "--auto"],
            cwd=str(_PACKAGE_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except Exception as e:
        return {
            "error": True,
            "kind": "unknown",
            "message": f"Failed to spawn auth subprocess: {e}",
        }

    return {
        "operation": "setup_oauth",
        "status": "browser_opening",
        "url": auth_url,
        "message": (
            "A browser tab should be opening at Google's consent screen. "
            "Approve the requested permissions there. Mise will save the token "
            "automatically (to macOS Keychain). Once you see 'Authorization Successful' "
            "in the browser, retry your original mise call."
        ),
        "cues": {
            "fallback": (
                "If the browser didn't open automatically, copy the 'url' field above "
                "and paste it into your browser manually."
            ),
            "log_path": str(log_path),
            "token_will_save_to": str(TOKEN_FILE),
        },
    }


def _port_is_free(port: int) -> bool:
    """Check if localhost:port is bindable. Returns True if free."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("localhost", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()
