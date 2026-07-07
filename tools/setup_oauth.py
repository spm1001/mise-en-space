"""
Setup OAuth — bootstrap a user's Google credentials from inside an MCP session.

The auth flow needs three things that don't normally happen during an MCP call:
- a Mac browser to open
- a localhost callback listener
- a wait of up to a few minutes for the user to click through

We solve this by minting the auth URL here (which persists the PKCE verifier)
and spawning `python -m auth --auto --url <url>` as a detached subprocess.
The subprocess never mints its own URL — it opens the browser (best-effort),
listens on localhost:3000, exchanges the code against the persisted verifier,
and saves the token via save_token(). The MCP tool returns immediately with
the auth URL inline so headless users can click it elsewhere (tunnel or
--code path).

User flow in Cowork:
1. Calls mise.do(operation="setup_oauth")
2. Browser tab opens (or they paste the URL we returned)
3. They approve at Google's consent screen
4. Subprocess saves token to Keychain
5. They retry their original mise call — now it works.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from jeton import get_auth_url

from cues_util import with_identity
from oauth_config import LOCAL_CREDENTIALS_FILE, OAUTH_PORT, SCOPES, TOKEN_FILE, port_is_free
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

    # A token blob existing is presence, not validity (has_token checks
    # Keychain/disk only). Before claiming already_authenticated, load the
    # creds through the same path the runtime uses — get_sync_client()
    # raises with a per-failure-mode diagnostic (corrupt JSON, no
    # refresh_token, refresh failed/revoked). On failure we fall through
    # to a fresh auth flow instead of sending the user into an
    # "authed!" → "auth failed" loop (mise-didage). The successful call
    # also eager-resolves identity for the cue below.
    stale_creds_diagnostic: str | None = None
    if has_token(TOKEN_FILE) and not force:
        try:
            from adapters.http_client import get_sync_client
            get_sync_client()
        except Exception as e:
            stale_creds_diagnostic = str(e)
        else:
            return {
                "operation": "setup_oauth",
                "status": "already_authenticated",
                "message": (
                    "An OAuth token is already present and loads cleanly. Try your "
                    "original mise call again. If it fails with an auth error, call "
                    "setup_oauth with force=true to re-auth."
                ),
                "cues": with_identity({
                    "token_location": str(TOKEN_FILE),
                }),
            }

    # Pre-flight: is the OAuth callback port free?
    if not port_is_free(OAUTH_PORT):
        return {
            "error": True,
            "kind": "network_error",
            "message": (
                f"Port {OAUTH_PORT} on localhost is already in use. "
                "OAuth needs this port for the callback listener. It may be an "
                "earlier setup_oauth listener still in its 5-minute window — "
                "wait for it to expire, or stop whatever else is using the port "
                "(often a Node dev server) and try again."
            ),
        }

    # Generate the URL exactly once — this persists the PKCE verifier next to
    # the token file. The subprocess must NOT mint its own URL: a second mint
    # overwrites that verifier, orphaning the URL we return here (challenge A
    # vs stored verifier B — the exchange fails and a consent round-trip is
    # burned; mise-zefahe). Single-flow principle: whoever generates the URL
    # owns the verifier; the subprocess only listens and exchanges.
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
        # Child inherits a duplicate of the fd at spawn; the parent's copy
        # closes in the finally so it doesn't leak in the long-running server.
        with open(log_path, "w") as log_fh:
            subprocess.Popen(
                [sys.executable, "-m", "auth", "--auto", "--url", auth_url],
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

    message = (
        "A browser tab should be opening at Google's consent screen (on "
        "machines with a browser). Approve the requested permissions there. "
        "A listener on localhost:3000 catches the callback for the next 5 "
        "minutes and saves the token automatically. Once you see "
        "'Authorization Successful' in the browser, retry your original mise call."
    )
    cues: dict[str, Any] = {
        "fallback": (
            "If no browser opened (headless box), open the 'url' field in any "
            "browser. The callback must reach localhost:3000 on the machine "
            "running mise — either run `ssh -L 3000:localhost:3000 <host>` "
            "before clicking, or after approving copy the localhost redirect "
            "URL from the address bar and run: "
            "uv run python -m auth --code '<redirect_url>'"
        ),
        "log_path": str(log_path),
        "token_will_save_to": str(TOKEN_FILE),
    }
    if stale_creds_diagnostic:
        cues["stale_creds_diagnostic"] = stale_creds_diagnostic
        message = (
            "A token was present but could not be loaded (see "
            "cues.stale_creds_diagnostic), so a fresh sign-in is needed. "
        ) + message

    return {
        "operation": "setup_oauth",
        "status": "reauthenticating_stale_creds" if stale_creds_diagnostic else "browser_opening",
        "url": auth_url,
        "message": message,
        "cues": cues,
    }
