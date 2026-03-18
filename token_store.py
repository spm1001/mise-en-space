"""
Token storage for mise-en-space OAuth tokens.

Storage priority:
1. macOS Keychain (service: mise-oauth-token) — persistent across installs
2. File (token.json in package root) — fallback for non-macOS

The token is a JSON blob (access_token, refresh_token, client_id, etc.).
Keychain stores it as the password field of a generic password entry.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

KEYCHAIN_SERVICE = "mise-oauth-token"


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

    If a Keychain entry exists, writes it to the fallback_path so that
    jeton.load_credentials() can read it as a file. If no Keychain entry,
    returns the fallback_path as-is (may or may not exist).
    """
    token_json = get_from_keychain()
    if token_json:
        fallback_path.write_text(token_json)
        return fallback_path
    return fallback_path


def save_token(token_path: Path) -> None:
    """After auth writes token.json, persist it to Keychain and remove the file."""
    if not token_path.exists():
        return
    token_json = token_path.read_text().strip()
    if store_to_keychain(token_json):
        token_path.unlink(missing_ok=True)
        print(f"  Token stored in macOS Keychain (service: {KEYCHAIN_SERVICE}).", file=sys.stderr)
    else:
        print(f"  Warning: Keychain storage failed. Token remains at {token_path}.", file=sys.stderr)


def has_token(fallback_path: Path) -> bool:
    """Check if a valid token exists anywhere."""
    if get_from_keychain():
        return True
    return fallback_path.exists()
