"""
Why ambient staff-directory placement is off, when it is (mise-hejeze).

Search rows and Gmail fetches place own-domain people from the directory
(adapters/people.py). An address the directory does not know — external,
departed, opted out, a group — is an honest absence, cached so a stranger is
not re-asked all session. A REFUSED read is a different fact: a token minted
before admin.directory.user.readonly joined the scopes gets a 403 on every
lookup, and until this module that refusal was cached as the same honest
absence, so placement could be dead on a machine with nothing saying so (a
July-vintage token on sameer-macbook-air, 28 Sep 2026).

So a refusal lives here, never in the profile cache, and both placement paths
turn it into one `people_unavailable` cue. It stands for _TTL_SECONDS, then
the directory is asked again. Neither "sticky until restart" nor "ask every
time" is right: the running server does not re-read its token from disk while
the old grant still refreshes (adapters/http_client.py reloads only on a
RefreshError), so a fix can land without this process noticing it, and a
refusal re-asked per address would cost a failed call per colleague per search.
"""

import time

from models import MiseError

_TTL_SECONDS = 300.0

# Measured 2026-09-28 on tube (narrow-scope refresh of a real ITV grant): the
# directory answers a token without the scope with this message, and the same
# grant WITH the scope with 200. Any other 403 leaves the cause unproven.
_SCOPE_MISSING = "insufficient authentication scopes"

_FIX = (
    "Fix: do(operation='setup_oauth', force=True). A server that is already "
    "running keeps its old token in memory, so start a fresh session if this "
    "cue persists after re-consenting."
)

_state: tuple[str, float] | None = None  # (cause in plain words, monotonic time)


def note(error: MiseError) -> None:
    """Remember a refused directory read, with its cause in plain words."""
    global _state
    details = error.details or {}
    if _SCOPE_MISSING in str(details.get("body", "")):
        cause = "this token lacks the admin.directory.user.readonly scope."
    else:
        cause = (
            f"the directory refused the read (HTTP {details.get('http_status', 403)}). "
            "The usual cause is a token minted before the "
            "admin.directory.user.readonly scope was added; if re-consenting does "
            "not clear it, the domain's Contact sharing setting may be off."
        )
    _state = (cause, time.monotonic())


def blocking() -> bool:
    """True while a recent refusal stands: skip the lookup rather than re-ask."""
    return _state is not None and time.monotonic() - _state[1] < _TTL_SECONDS


def reason() -> str | None:
    """The standing refusal's cause, or None when the directory last answered."""
    return _state[0] if _state else None


def clear() -> None:
    """Forget the refusal — the directory answered, or the token changed."""
    global _state
    _state = None


def cue(unplaced: int) -> str | None:
    """The `people_unavailable` cue for `unplaced` own-domain addresses, or None."""
    cause = reason()
    if not cause or not unplaced:
        return None
    return (
        f"Directory placement skipped for {unplaced} own-domain address(es): "
        f"{cause} {_FIX} Until then an own-domain address with no `people` entry "
        "is UNPLACED, not absent — do not report it as external or opted out."
    )
