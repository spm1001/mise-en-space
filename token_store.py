"""
Token storage for mise-en-space OAuth tokens.

Storage priority:
0. MISE_TOKEN_PATH env override — caller-owned credential file (see below)
1. macOS Keychain (service: mise-oauth-token) — persistent across installs
2. File (token.json in package root) — fallback for non-macOS

The token is a JSON blob (access_token, refresh_token, client_id, etc.).
Keychain stores it as the password field of a generic password entry.

MISE_TOKEN_PATH override: when set, mise runs as a guest on a credential
file owned by the embedding caller (e.g. Cornichon's ADC file). The path
is authoritative — no Keychain fallback, even if the file is missing
(falling through would silently switch identity to the personal token).
Guest mode also means persist-nothing: store_to_keychain is a no-op, so
neither auth flows nor identity enrichment can clobber the user's own
mise Keychain entry with the caller's (differently-scoped) token.

OAuth-client seam (mise-nujina): with MISE_EN_SPACE_OAUTH_CLIENT and/or MISE_EN_SPACE_DATA_DIR
set, the Keychain entry is keyed by the supplied client, an empty store adopts
a pre-seam token minted by that same client (a copy — the original stays), and
a token minted by any other client is refused. Neither set: unchanged.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from oauth_config import (
    PRE_SEAM_DATA_DIRS,
    client_from_env,
    configured_client_id,
    data_dir_from_env,
)

logger = logging.getLogger(__name__)

KEYCHAIN_SERVICE = "mise-oauth-token"

# The Keychain services each flavour used before the OAuth-client seam
# (mise-nujina): adoption reads them, never writes them. Written through
# KEYCHAIN_SERVICE so the flavour transform's rename leaves a clean pair
# (it collapses to one name in the home build) instead of mangling a literal.
PRE_SEAM_KEYCHAIN_SERVICES = tuple(dict.fromkeys((KEYCHAIN_SERVICE, "mise-home-oauth-token")))

# Env var naming a caller-owned token file (guest mode). Authoritative
# when set: no Keychain reads, no Keychain writes, no migration.
OVERRIDE_ENV = "MISE_TOKEN_PATH"

# --- Constructor-selected identity (mise_en_space facade, mise-dareti) ------
#
# A library consumer selects the identity in code — Mise(credentials=...),
# Mise(token_path=...), Mise(ambient=True) — instead of through env vars.
# The state lives here, in process, and override_path()/ambient_mode()
# consult it, so every downstream gate (guest-mode teaching errors, the
# ambient search narrowing and mailbox-op refusals) fires identically
# whichever way the mode was selected. Env vars are never written: a
# constructor mutating the environment would leak the choice into
# subprocesses and into state the caller believes they own.
#
# One identity per process is the architecture (single-user clients,
# lru_cached services), so configure_identity() replaces wholesale —
# last call wins — and the facade clears the HTTP-client singletons
# after each call so stale credentials can't linger in a live client.
_CONFIGURED: dict[str, Any] = {}


def configure_identity(
    *,
    credentials: Any | None = None,
    token_path: Path | str | None = None,
    ambient: bool = False,
) -> None:
    """Select the process identity from code; with no arguments, clear it.

    At most one selector may be passed, and none may be passed while an
    identity env var (MISE_TOKEN_PATH / MISE_CREDENTIALS) is set — the
    same no-honest-precedence rule ambient_mode() applies to env-env
    collisions. A silent winner would be an accepted-and-dropped
    identity, this codebase's characteristic bug.
    """
    chosen = [
        name
        for name, given in (
            ("credentials", credentials is not None),
            ("token_path", token_path is not None),
            ("ambient", bool(ambient)),
        )
        if given
    ]
    if len(chosen) > 1:
        raise ValueError(
            f"Pass at most one of credentials/token_path/ambient — got "
            f"{' and '.join(chosen)}. They select different identities."
        )
    if chosen:
        for env_name in (OVERRIDE_ENV, AMBIENT_ENV):
            if os.environ.get(env_name):
                raise ValueError(
                    f"{env_name} is set in the environment while "
                    f"'{chosen[0]}' was passed in code — these select "
                    f"different identities and there is no honest "
                    f"precedence. Unset {env_name} or drop the argument."
                )
    _CONFIGURED.clear()
    if credentials is not None:
        _CONFIGURED["credentials"] = credentials
    elif token_path is not None:
        _CONFIGURED["token_path"] = Path(token_path)
    elif ambient:
        _CONFIGURED["ambient"] = True


def configured_credentials() -> Any | None:
    """The credentials object passed in code, or None (the common case)."""
    return _CONFIGURED.get("credentials")


def injected_refresh_refusal() -> FileNotFoundError:
    """Teaching error for a refresh refusal on constructor-injected credentials.

    http_client's reload-then-retry path is token-file medicine: a
    caller-owned credentials OBJECT has no path to re-read, and mise
    cannot re-mint what it never minted.
    """
    return FileNotFoundError(
        "The credentials passed to mise in code (Mise(credentials=...)) were "
        "refused on refresh. mise cannot re-mint a caller-owned credential — "
        "construct a new Mise with fresh credentials, or use token_path= / "
        "ambient=True, whose sources mise can re-read itself."
    )


def override_path() -> Path | None:
    """Return the caller-supplied token path, or None when not in guest mode."""
    configured = _CONFIGURED.get("token_path")
    if configured is not None:
        return Path(configured)
    raw = os.environ.get(OVERRIDE_ENV)
    return Path(raw) if raw else None


# Env switch for ambient Application Default Credentials (mise-wasagu):
# MISE_CREDENTIALS=ambient makes mise mint credentials via
# google.auth.default() — Cloud Run metadata server, workload identity,
# and GOOGLE_APPLICATION_CREDENTIALS all resolve through that one call.
AMBIENT_ENV = "MISE_CREDENTIALS"


def ambient_mode() -> bool:
    """True when the caller opted into ambient ADC (MISE_CREDENTIALS=ambient).

    Explicit opt-in only — a missing token file NEVER falls through to
    ambient discovery. That would be a silent identity switch, the same
    hazard the MISE_TOKEN_PATH design refuses (see module docstring).

    Misconfiguration raises rather than picking a winner: an unrecognised
    value would otherwise be accepted-and-dropped (this codebase's
    characteristic bug), and 'both modes set' has no honest precedence —
    they name different identities.
    """
    if _CONFIGURED.get("ambient"):
        return True
    raw = os.environ.get(AMBIENT_ENV)
    if not raw:
        return False
    if raw != "ambient":
        raise ValueError(
            f"{AMBIENT_ENV}={raw!r} is not recognised — the only supported "
            "value is 'ambient' (Application Default Credentials). Unset it "
            "to use the token file."
        )
    if override_path() is not None:
        raise ValueError(
            f"Both {AMBIENT_ENV}=ambient and {OVERRIDE_ENV} are set — these "
            "select different identities (platform service account vs "
            "caller-owned token file). Unset one."
        )
    return True

# Legacy token location (package root). Used for migration from
# versioned plugin cache dirs to stable data dir.
_LEGACY_TOKEN_PATH = Path(__file__).parent / "token.json"


def _has_keychain() -> bool:
    """Check if macOS Keychain is available."""
    return sys.platform == "darwin" and os.path.exists("/usr/bin/security")


def keychain_service() -> str:
    """The Keychain service this process stores its token under.

    A client supplied from outside (MISE_EN_SPACE_OAUTH_CLIENT) keys the entry by its
    client_id: one engine serving two Workspaces on one Mac must not share a
    single entry, and the flavour-renamed constant only separates builds, not
    configurations. Unset, it is the flavour's pre-seam service unchanged.
    """
    if client_from_env():
        return f"{KEYCHAIN_SERVICE}:{configured_client_id()}"
    return KEYCHAIN_SERVICE


def get_from_keychain(service: str | None = None) -> str | None:
    """Get token JSON from macOS Keychain.

    The `security` CLI hex-encodes long passwords. If the output looks
    like hex (all hex chars, no whitespace), decode it first.
    """
    if not _has_keychain():
        return None
    service = service or keychain_service()
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", service, "-w"],
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
    """Store token JSON in macOS Keychain.

    No-op in guest mode (MISE_TOKEN_PATH set): the credential belongs to
    the embedding caller, and persisting it here would overwrite the
    user's own mise token with one of different scope/identity.
    """
    if override_path() is not None:
        logger.debug("Keychain write skipped: %s is set (guest mode)", OVERRIDE_ENV)
        return False
    if not _has_keychain():
        return False
    user = os.environ.get("USER", "")
    service = keychain_service()
    try:
        # Remove existing entry (ignore if not found)
        subprocess.run(
            ["security", "delete-generic-password", "-a", user, "-s", service],
            capture_output=True, check=False,
        )
        subprocess.run(
            ["security", "add-generic-password", "-a", user, "-s", service, "-w", token_json],
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
            ["security", "delete-generic-password", "-a", user, "-s", keychain_service()],
            capture_output=True, check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def resolve_token_path(fallback_path: Path) -> Path:
    """Return a path to a token.json file, materializing from Keychain if needed.

    Search order:
    0. MISE_TOKEN_PATH env override — returned unconditionally when set,
       even if the file is missing (the credential loader's diagnostics
       fire on the override path; falling through to Keychain would be a
       silent identity switch to the user's personal token)
    1. macOS Keychain → materialize to fallback_path
    2. fallback_path (typically plugin data dir or package root)
    3. _PACKAGE_ROOT/token.json (legacy — versioned plugin cache)
    4. Only when the OAuth-client seam is in use (MISE_EN_SPACE_OAUTH_CLIENT or
       MISE_EN_SPACE_DATA_DIR set): a pre-seam flavour store holding a token minted
       by the configured client — see find_pre_seam_token (mise-nujina)

    If a token is found at a legacy location but not at fallback_path,
    it is copied forward (migration from versioned cache to stable data dir).

    With the seam in use, a token minted by a DIFFERENT client than the
    configured one is refused rather than returned: it belongs to another
    Workspace, and loading it would act as that identity under this one's name.
    """
    override = override_path()
    if override is not None:
        return override
    path = _resolve_own_store(fallback_path)
    _refuse_foreign_token(path)
    return path


def _resolve_own_store(fallback_path: Path) -> Path:
    token_json = get_from_keychain()
    if token_json:
        _write_private(fallback_path, token_json)
        return fallback_path

    if fallback_path.exists():
        return fallback_path

    # Check legacy location (package root) if fallback_path is elsewhere
    legacy_path = _LEGACY_TOKEN_PATH
    if legacy_path != fallback_path and legacy_path.exists():
        # Migrate: copy to stable location so future versions find it
        _write_private(fallback_path, legacy_path.read_text())
        return fallback_path

    adopted = find_pre_seam_token()
    if adopted is not None:
        where, token_json = adopted
        _write_private(fallback_path, token_json)
        logger.warning(
            "Adopted the pre-seam token from %s into %s — it was minted by the "
            "configured OAuth client, so no re-consent is needed. The original "
            "is left in place for older mise versions still reading it.",
            where, fallback_path,
        )
    return fallback_path


def _seam_in_use() -> bool:
    return client_from_env() or data_dir_from_env()


def _client_id_of(token_json: str) -> str | None:
    try:
        data = json.loads(token_json)
    except json.JSONDecodeError:
        return None
    cid = data.get("client_id") if isinstance(data, dict) else None
    return cid if isinstance(cid, str) else None


def find_pre_seam_token() -> tuple[str, str] | None:
    """(where, token_json) for a pre-seam token minted by the configured client.

    Consulted only with the seam in use and this process's own store empty,
    so an unset environment behaves exactly as before. Matching on client_id
    is what keeps adoption honest: each flavour's old store holds a different
    Workspace's token, and a token only refreshes against the client that
    minted it anyway. Nothing here writes to a pre-seam store.
    """
    if not _seam_in_use():
        return None
    want = configured_client_id()
    if want is None:
        return None
    for service in PRE_SEAM_KEYCHAIN_SERVICES:
        raw = get_from_keychain(service)
        if raw and _client_id_of(raw) == want:
            return f"Keychain service {service}", raw
    for data_dir in PRE_SEAM_DATA_DIRS:
        path = data_dir / "token.json"
        if not path.is_file():
            continue
        try:
            raw = path.read_text()
        except OSError as e:
            logger.warning("Pre-seam token at %s unreadable, skipped: %s", path, e)
            continue
        if _client_id_of(raw) == want:
            return str(path), raw
    return None


def _write_private(path: Path, text: str) -> None:
    """Write a credential file readable by its owner only.

    os.open's mode applies only when it CREATES the file, so an existing file
    keeps whatever mode it had — the macOS Keychain materialisation rewrites
    the same token.json on every server start, and files first written by
    Path.write_text sat at 0644 for months (mise-zuzogu). The fchmod tightens
    an existing file too, after the truncate and before the secret is written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def _refuse_foreign_token(path: Path) -> None:
    """With the seam in use, refuse a token another OAuth client minted."""
    if not _seam_in_use() or not path.is_file():
        return
    try:
        minted_by = _client_id_of(path.read_text())
    except OSError:
        return  # the credential loader diagnoses unreadable files
    want = configured_client_id()
    if minted_by and want and minted_by != want:
        raise FileNotFoundError(
            f"The token at {path} was minted by OAuth client "
            f"{minted_by.split('-')[0]}…, but the configured client is "
            f"{want.split('-')[0]}… — two different Workspace identities, and "
            "mise will not act as one under the other's name. Point "
            "MISE_EN_SPACE_DATA_DIR at this client's own store, or re-authenticate: "
            'call mise.do(operation="setup_oauth", force=True).'
        )


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
    # The auth flow's file arrives at the process umask (0644 on a stock
    # machine), and on Linux it stays as the designed store — tighten it
    # before anything else touches it (mise-zuzogu).
    token_path.chmod(0o600)
    raw = token_path.read_text().strip()
    try:
        token = json.loads(raw)
        access_token = token.get("token") or token.get("access_token")
        if access_token and "_identity" not in token:
            email = _fetch_user_email(access_token)
            if email:
                token["_identity"] = {"email": email}
                raw = json.dumps(token)
                _write_private(token_path, raw)
                print(f"  Identity resolved: {email}", file=sys.stderr)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(
            "Token enrichment skipped (JSON/IO error): %s: %s",
            type(e).__name__, e,
        )

    if store_to_keychain(raw):
        token_path.unlink(missing_ok=True)
        print(f"  Token stored in macOS Keychain (service: {keychain_service()}).", file=sys.stderr)
    elif override_path() is not None:
        # Guest mode: the credential belongs to the embedding caller; the file
        # at its path is the designed home, not a fallback.
        print(f"  Token stored as a file at {token_path} (guest mode — no Keychain write by design).", file=sys.stderr)
    elif not _has_keychain():
        # No Keychain on this platform (e.g. Linux): file storage is the
        # DESIGNED path, not a failure. The old "Keychain storage failed"
        # wording read as a defect for entirely normal behaviour (mise-petaga).
        print(f"  Token stored as a file at {token_path} (this platform has no Keychain — file is the designed store).", file=sys.stderr)
    else:
        # Keychain IS present but the write genuinely failed — a real problem.
        print(f"  Warning: Keychain is present but the token write failed. Token remains at {token_path}.", file=sys.stderr)


def has_token(fallback_path: Path) -> bool:
    """Check if a valid token exists anywhere."""
    override = override_path()
    if override is not None:
        return override.exists()
    if get_from_keychain():
        return True
    if fallback_path.exists():
        return True
    # Check legacy location (package root)
    legacy_path = _LEGACY_TOKEN_PATH
    if legacy_path != fallback_path and legacy_path.exists():
        return True
    # Seam in use and own store empty: an adoptable pre-seam token counts,
    # or setup_oauth would send a signed-in user through consent again.
    return find_pre_seam_token() is not None
