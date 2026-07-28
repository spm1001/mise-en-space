"""Every doc surface that lists do() operations must match what dispatch actually holds.

The op list lives in five places and drifts silently. `test_tool_description_budget.py`
already polices one of them (DO_DESCRIPTION_FULL, via the live tool description). This
covers the other four — two markdown docs and two rendered tables — plus the stated
count, which is the part that has gone wrong before: `.bon/understanding.md` records
cold-start docs reading "14 ops" while the tool had 15.

Adding an operation should turn this red, naming the surfaces you forgot.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools import OPERATIONS

REPO = Path(__file__).resolve().parents[2]

# Surfaces that must name every operation. Path, and a human label for the failure.
NAME_SURFACES = [
    ("CLAUDE.md", "the do() row in the MCP Tool Surface table"),
    ("README.md", "the do() row in the tools table"),
    ("skills/mise/SKILL.md", "the Operations table"),
    ("resources/docs.py", "docs_do()"),
]

# Surfaces that also state a count in prose, e.g. "18 ops" / "All 17".
COUNT_SURFACES = ["CLAUDE.md", "README.md"]

# A count can sit on EITHER side of the word: "18 ops" but also
# "| `do()` operations | All 17 |". The first version of this test only matched
# number-then-word and sailed past a real 17-vs-18 contradiction on CLAUDE.md:130 —
# a fabricated-op probe exercised a different path than the real bug took. Match both.
COUNT_RE = re.compile(
    r"\b(\d+)\s+(?:safe\s+)?(?:ops|operations)\b"      # "18 ops", "6 safe ops"
    r"|\b(?:ops|operations)\b[^\n]{0,20}?\b(\d+)\b"    # "operations | All 17" (crosses the table pipe)
)


def _read(rel: str) -> str:
    p = REPO / rel
    assert p.exists(), f"{rel} is missing — update this test if the file moved"
    return p.read_text()


@pytest.mark.parametrize("rel,label", NAME_SURFACES, ids=[s[0] for s in NAME_SURFACES])
def test_surface_names_every_operation(rel: str, label: str) -> None:
    text = _read(rel)
    missing = sorted(op for op in OPERATIONS if f"`{op}`" not in text and op not in text)
    assert not missing, (
        f"{rel} ({label}) does not mention: {missing}\n"
        f"dispatch holds {len(OPERATIONS)} ops. Update this surface, and check the others "
        f"listed in tests/unit/test_doc_operation_parity.py — they drift together."
    )


@pytest.mark.parametrize("rel", COUNT_SURFACES)
def test_stated_operation_count_is_right(rel: str) -> None:
    """Two counts are legitimate in these docs: the full op set, and the remote-safe
    subset. Any other number sitting next to 'ops'/'operations' is drift."""
    from tools.remote import REMOTE_ALLOWED_OPS

    legitimate = {len(OPERATIONS), len(REMOTE_ALLOWED_OPS)}
    text = _read(rel)
    stated = {int(g) for m in COUNT_RE.finditer(text) for g in m.groups() if g}
    assert stated, f"{rel} states no operation count — expected a phrase like '{len(OPERATIONS)} ops'"
    wrong = sorted(n for n in stated if n not in legitimate)
    assert not wrong, (
        f"{rel} states operation count(s) {wrong}, but the only legitimate counts are "
        f"{sorted(legitimate)} — {len(OPERATIONS)} dispatched, "
        f"{len(REMOTE_ALLOWED_OPS)} allowed in remote mode."
    )


def test_no_surface_invents_an_operation() -> None:
    """A doc naming an op that no longer dispatches is worse than one missing an op —
    it sends a caller at something that will be refused. Guards removals, not additions."""
    table_row = re.compile(r"^\|\s*`(\w+)`\s*\|", re.M)
    for rel in ("skills/mise/SKILL.md", "resources/docs.py"):
        text = _read(rel)
        named = {m.group(1) for m in table_row.finditer(text)}
        # Only judge rows that look like op rows: intersect with a generous candidate set.
        suspects = {n for n in named if f"do(operation=\"{n}\"" in text or n in OPERATIONS}
        ghosts = sorted(suspects - set(OPERATIONS))
        assert not ghosts, f"{rel} documents operations that no longer dispatch: {ghosts}"
