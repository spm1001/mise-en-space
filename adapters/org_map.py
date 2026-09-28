"""
Coarse division for a colleague, from the hand-maintained org map (mise-fajabe).

Split out of adapters/people.py on 2026-09-28 (mise-hejeze) to give the
placement code room under the 500-line module cap; people.py re-exports
division_for, so callers and tests import it from either module.
"""

import json
from pathlib import Path
from typing import Any

# --- Coarse division, from the hand-maintained org map ---------------------
#
# `org_map.json` at the package root is DATA, deliberately with no code in it,
# so it can be reviewed, diffed or replaced without reading Python — and so it
# rides a normal release like credentials.json, the other tenant-keyed fact
# mise already ships. Domain-keyed: the mechanism is generic, only the data is
# tenant-specific, so the family kit's mise finds no entry for its own domain
# and the file is simply inert there rather than needing a build-time swap.

_ORG_MAP_FILE = Path(__file__).parent.parent / "org_map.json"
_org_map: dict[str, Any] | None = None


def _load_org_map() -> dict[str, Any]:
    """Read and cache org_map.json. A broken map costs divisions, never a fetch."""
    global _org_map
    if _org_map is None:
        try:
            _org_map = json.loads(_ORG_MAP_FILE.read_text()).get("domains") or {}
        except Exception:
            # Absent or malformed: degrade to no divisions at all. This is
            # decoration on a directory read that has already succeeded.
            _org_map = {}
    return _org_map


def division_for(email: str, department: str | None) -> str | None:
    """Coarse division for a colleague, or None. NEVER guesses.

    Exact department match wins; an ordered substring list is the fallback.
    An unmapped department yields nothing, because a wrong division is worse
    than none — 'Strategy, Policy & Regulation' is the corporate centre and a
    keyword rule would file it under Commercial, misplacing precisely the
    senior people it matters most to place correctly.
    """
    if not department or "@" not in (email or ""):
        return None
    entry = _load_org_map().get(email.rsplit("@", 1)[-1].lower())
    if not entry:
        return None
    exact = (entry.get("departments") or {}).get(department)
    if exact:
        return str(exact)  # the map is untyped JSON; its values are strings
    low = department.lower()
    for pair in entry.get("patterns") or []:
        if len(pair) == 2 and pair[0] in low:
            return str(pair[1])
    return None
