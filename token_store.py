"""
Token storage for mise-en-space OAuth tokens.

Storage priority:
1. macOS Keychain (service: mise-oauth-token) — persistent across installs
2. File (token.json in package root) — fallback for non-macOS

The token is a JSON blob (access_token, refresh_token, client_id, etc.).
Keychain stores it as the password field of a generic password entry.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

KEYCHAIN_SERVICE = "mise-oauth-token"

# Legacy token location (package root). Used for migration from
# versioned plugin cache dirs to stable data dir.
_LEGACY_TOKEN_PATH = Path(__file__).parent / "token.json"


def _has_keychain() -> bool:
    """Check if macOS Keychain is available."""
    return sys.platform == "darwin" and os.path.exists("/usr/bin/security")


def get_from_keychain() -> str | None:
    """Get token JSON from macOS Keychain.

    The `security` CLI hex-encodes long passwords. If the output looks
    like hex (all hex chars, no whitespace), decode it first.
    """
    if not _has_keychain():
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, check=True,
        )
        raw = result.stdout.strip()
        # Try as plain JSON first
        try:
            json.loads(raw)
            return raw
        except json.JSONDecodeError:
            pass
        # Try hex-decoding (security CLI encodes long values as hex)
        try:
            decoded = bytes.fromhex(raw).decode("utf-8")
            json.loads(decoded)
            return decoded
        except (ValueError, json.JSONDecodeError):
            pass
        return None
    except subprocess.CalledProcessError:
        return None


def store_to_keychain(token_json: str) -> bool:
    """Store token JSON in macOS Keychain."""
    if not _has_keychain():
        return False
    user = os.environ.get("USER", "")
    try:
        # Remove existing entry (ignore if not found)
        subprocess.run(
            ["security", "delete-generic-password", "-a", user, "-s", KEYCHAIN_SERVICE],
            capture_output=True, check=False,
        )
        subprocess.run(
            ["security", "add-generic-password", "-a", user, "-s", KEYCHAIN_SERVICE, "-w", token_json],
            capture_output=True, check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def delete_from_keychain() -> bool:
    """Remove token from macOS Keychain."""
    if not _has_keychain():
        return False
    user = os.environ.get("USER", "")
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-a", user, "-s", KEYCHAIN_SERVICE],
            capture_output=True, check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def resolve_token_path(fallback_path: Path) -> Path:
    """Return a path to a token.json file, materializing from Keychain if needed.

    Search order:
    1. macOS Keychain → materialize to fallback_path
    2. fallback_path (typically plugin data dir or package root)
    3. _PACKAGE_ROOT/token.json (legacy — versioned plugin cache)

    If a token is found at a legacy location but not at fallback_path,
    it is copied forward (migration from versioned cache to stable data dir).
    """
    token_json = get_from_keychain()
    if token_json:
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        fallback_path.write_text(token_json)
        return fallback_path

    if fallback_path.exists():
        return fallback_path

    # Check legacy location (package root) if fallback_path is elsewhere
    legacy_path = _LEGACY_TOKEN_PATH
    if legacy_path != fallback_path and legacy_path.exists():
        # Migrate: copy to stable location so future versions find it
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        fallback_path.write_text(legacy_path.read_text())
        return fallback_path

    return fallback_path


def _fetch_user_email(access_token: str) -> str | None:
    """Resolve the authenticated user's email via Drive's about endpoint.

    Drive `about?fields=user` returns the authenticated user's emailAddress and
    works with the `auth/drive` scope mise already has — no extra OAuth scope
    needed. Returns None on any failure; enrichment is best-effort.
    """
    try:
        import httpx
        resp = httpx.get(
            "https://www.googleapis.com/drive/v3/about",
            params={"fields": "user(emailAddress)"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=5.0,
        )
        resp.raise_for_status()
        user = resp.json().get("user") or {}
        email = user.get("emailAddress")
        return email if isinstance(email, str) else None
    except Exception as e:
        logger.warning(
            "Token enrichment via Drive about failed: %s: %s",
            type(e).__name__, e,
        )
        return None


def save_token(token_path: Path) -> None:
    """After auth writes token.json, persist it to Keychain and remove the file.

    Before storing, enrich with `_identity.email` resolved via userinfo.get.
    The email is cached in the token JSON so cues._identity reads are cheap.
    Enrichment is best-effort — userinfo failures don't block save.
    """
    if not token_path.exists():
        return
    raw = token_path.read_text().strip()
    try:
        token = json.loads(raw)
        access_token = token.get("token") or token.get("access_token")
        if access_token and "_identity" not in token:
            email = _fetch_user_email(access_token)
            if email:
                token["_identity"] = {"email": email}
                raw = json.dumps(token)
                token_path.write_text(raw)
                print(f"  Identity resolved: {email}", file=sys.stderr)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(
            "Token enrichment skipped (JSON/IO error): %s: %s",
            type(e).__name__, e,
        )

    if store_to_keychain(raw):
        token_path.unlink(missing_ok=True)
        print(f"  Token stored in macOS Keychain (service: {KEYCHAIN_SERVICE}).", file=sys.stderr)
    else:
        print(f"  Warning: Keychain storage failed. Token remains at {token_path}.", file=sys.stderr)


def has_token(fallback_path: Path) -> bool:
    """Check if a valid token exists anywhere."""
    if get_from_keychain():
        return True
    if fallback_path.exists():
        return True
    # Check legacy location (package root)
    legacy_path = _LEGACY_TOKEN_PATH
    return legacy_path != fallback_path and legacy_path.exists()
