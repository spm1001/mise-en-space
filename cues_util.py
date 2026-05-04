"""
Identity self-disclosure for response cues.

The authenticated user email is cached per-process and resolved eagerly when
the sync client initialises. This keeps response serialisation pure (no HTTP
in `to_dict()`) while still surfacing identity in every cue block.

Resolution path:
- If the materialised token already has `_identity.email` (set at OAuth time
  by token_store.save_token), read it cheaply.
- Otherwise (legacy tokens predating the enrichment), call Drive's `about`
  endpoint via the just-initialised sync client and backfill Keychain so
  future processes get it for free.

Lives at root level alongside other shared utilities (filters, validation,
retry) — identity disclosure is a crosscutting concern, not a model concern.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

_cached_user_email: str | None = None
_resolved: bool = False


class _SyncClientProto(Protocol):
    """Duck-typed sync client interface — avoids importing adapters/http_client."""
    def get_json(
        self, url: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...


def current_user_email() -> str | None:
    """Return the cached authenticated user email, or None if unresolved.

    Pure cache read — no I/O. The cache is populated by
    `resolve_user_email_eager()` which the sync client invokes at init.
    """
    return _cached_user_email if _resolved else None


def with_identity(cues: dict[str, Any]) -> dict[str, Any]:
    """Return a new cues dict with `_identity` injected if resolvable.

    Self-disclosing identity in every response so callers can tell which
    Google account answered when multiple Workspace connectors are loaded.
    Pure: never mutates the input dict, never does I/O.
    """
    email = current_user_email()
    if not email:
        return dict(cues)
    return {**cues, "_identity": {"email": email}}


def resolve_user_email_eager(
    client: _SyncClientProto, token_path: Path
) -> None:
    """Populate the identity cache. Called once at sync client init.

    Idempotent — re-call is a no-op once resolved (success or failure).
    Failures are logged at WARN and don't propagate; identity stays None.
    """
    global _cached_user_email, _resolved
    if _resolved:
        return
    try:
        if not token_path.exists():
            logger.debug("Identity unresolved: no token file at %s", token_path)
            return

        token = json.loads(token_path.read_text())
        identity = token.get("_identity") or {}
        email = identity.get("email")
        if isinstance(email, str):
            _cached_user_email = email
            return

        # Legacy token — backfill via Drive about. The sync client has just
        # finished credential loading/refresh, so `get_json` will use a fresh
        # access token. After the refresh, jeton has rewritten the file,
        # so re-read before merging in `_identity`.
        result = client.get_json(
            "https://www.googleapis.com/drive/v3/about",
            params={"fields": "user(emailAddress)"},
        )
        email = (result.get("user") or {}).get("emailAddress")
        if isinstance(email, str):
            from token_store import store_to_keychain
            token = json.loads(token_path.read_text())
            token["_identity"] = {"email": email}
            store_to_keychain(json.dumps(token))
            _cached_user_email = email
            logger.info("Identity backfilled from Drive about: %s", email)
        else:
            logger.warning(
                "Drive about returned no emailAddress; identity stays None"
            )
    except Exception as e:
        logger.warning(
            "Identity resolution failed: %s: %s", type(e).__name__, e
        )
    finally:
        _resolved = True


def clear_user_email_cache() -> None:
    """Reset cached identity (for testing or after re-auth)."""
    global _cached_user_email, _resolved
    _cached_user_email = None
    _resolved = False
