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
turn it into one `people_unavailable` cue. A lookup that merely FAILED (a
dropped connection — seen live in one of six cold fetches, 28 Sep) is not
cached either: lookups pause for _FAILURE_PAUSE_SECONDS, then retry, and the
same cue says so. It stands for _TTL_SECONDS, then
the directory is asked again. Neither "sticky until restart" nor "ask every
time" is right: the running server does not re-read its token from disk while
the old grant still refreshes (adapters/http_client.py reloads only on a
RefreshError), so a fix can land without this process noticing it, and a
refusal re-asked per address would cost a failed call per colleague per search.
"""

import time

from models import MiseError

_TTL_SECONDS = 300.0
# After a FAILED lookup, pause briefly too: failures are uncached, so a hanging
# directory would otherwise add the 60 s request timeout to every search.
_FAILURE_PAUSE_SECONDS = 30.0

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
_failure: tuple[str, float] | None = None  # last failed (not refused) lookup, and when


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
    """True while a recent refusal (or, briefly, a failure) stands: skip, don't re-ask."""
    now = time.monotonic()
    return (_state is not None and now - _state[1] < _TTL_SECONDS) or (
        _failure is not None and now - _failure[1] < _FAILURE_PAUSE_SECONDS)


def reason() -> str | None:
    """The standing refusal's cause, or None when the directory last answered."""
    return _state[0] if _state else None


def note_failure(detail: str) -> None:
    """Remember why a lookup FAILED (a dropped connection, a 5xx) — not a refusal:
    lookups pause for _FAILURE_PAUSE_SECONDS, then the address is asked again."""
    global _failure
    _failure = (detail, time.monotonic())


def clear() -> None:
    """Forget the refusal — the directory answered, or the token changed."""
    global _state, _failure
    _state = _failure = None


def cue(unplaced: int) -> str | None:
    """The `people_unavailable` cue for `unplaced` own-domain addresses, or None.

    A standing refusal names its cause and fix; otherwise the gap is a failed
    call (or a refusal cleared by a sibling thread mid-flight), which the next
    call retries. Either way the addresses are unplaced, never absent.
    """
    if not unplaced:
        return None
    cause = reason()
    if cause:
        return (
            f"Directory placement skipped for {unplaced} own-domain address(es): "
            f"{cause} {_FIX} Until then an own-domain address with no `people` "
            "entry is UNPLACED, not absent — do not report it as external or opted out."
        )
    why = f" ({_failure[0]})" if _failure else ""
    return (
        f"Directory placement incomplete for {unplaced} own-domain address(es): "
        f"a lookup failed{why}, and lookups pause for {int(_FAILURE_PAUSE_SECONDS)} "
        "seconds after a failure. They are UNPLACED, not absent — do not report "
        "them as external or opted out; they are asked again after the pause."
    )
