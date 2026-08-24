"""Guidance freshness: a negation naming shipped vocabulary must be adjudicated.

The rot species (mise-gaseve): "mise can't X" outliving the release that
shipped X. Only negations rot — capabilities get added and essentially never
removed, so "can do X" stays true indefinitely while "can't do X" decays at
the speed of the roadmap. And stale guidance never FAILS — it just taxes
every reader who obeys it (the folder row in instructions.md cost a real
session a hand-rolled Drive-API script the day this item was filed,
mise-kagejo). Rot that fails gets fixed within the hour; rot that merely
taxes is immortal unless something looks for it. This test is that something.

DECISION (recorded per the brief's step 3): this FAILS CI, but only on the
narrow shape — a negation line that names a shipped op or sharp param AND
carries no date AND is not enrolled below. Everything else stays silent.

The allowlist is a ratchet, not an exemption: enrolling a genuinely-true
negation (a platform limit, a design boundary) is a deliberate act with a
reason string, and an entry that stops matching any line fails too, so the
list cannot silently rot in either direction.

Honest limit: regex catches EXPLICIT negations. The original folder rot was
implicit — "when bypassing mise (e.g. folder creation)" asserts the gap by
example, with no negation token — and no line-level pattern catches that
shape. The notes-side sibling (~/notes/practices/mise/check-mise-claims.py)
covers ~/notes; this test covers the vendored guidance. Keep them separate
and honest about which surface each reads (the brief's closing rule).
"""

import re
from pathlib import Path

from tools import OPERATIONS

_REPO = Path(__file__).parents[2]

# The vendored guidance surfaces — what ships to every mise user. CLAUDE.md is
# deliberately excluded: it is a maintainer document dense with true API
# negatives ("keepForever is silently ignored"), and sweeping it would bury
# the signal in enrolments. tests/ and docs/ never ship at all.
GUIDANCE_FILES = ("instructions.md", "skills/mise/SKILL.md", "README.md")

# Sharp parameter names only — distinctive enough that a backtick/`=` match
# means the line is really about the parameter. Deliberately a hand list:
# introspecting the tool signatures needs server.py's FastMCP wrappers, and
# the blunt do() params (content, title, find, action) collide with prose.
# Add a name here when a new sharp param ships.
SHARP_PARAMS = (
    "raw_query", "raw", "tabs", "suggestions", "recursive", "supersede",
    "file_path", "doc_type", "include", "range", "folder_id", "attachment",
    "max_results", "base_path", "restore_comment", "page_setup",
)

_NEGATION = re.compile(
    r"(?i)\b(can(?:no|')t|cannot|can not|doesn'?t|does not|don'?t|won'?t|"
    r"not supported|unsupported|not available|isn'?t available|no way to|"
    r"not possible|never|not yet)\b"
)
_DATED = re.compile(r"\b20\d\d-\d\d-\d\d\b")


def _vocabulary_in(line: str) -> list[str]:
    """Shipped names this line is actually ABOUT (not mere prose collision).

    An op counts only in call- or code-form (`op`, do(op), operation="op");
    a param only in backtick or keyword form (`param`, param=). This is the
    discriminator that keeps 'not a copy' in prose from firing on op:copy.
    """
    found = []
    for op in OPERATIONS:
        if f"`{op}" in line or f"do({op}" in line or f'operation="{op}"' in line:
            found.append(f"op:{op}")
    for p in SHARP_PARAMS:
        if f"`{p}" in line or f"{p}=" in line:
            found.append(f"param:{p}")
    return sorted(set(found))


def _normalize(line: str) -> str:
    return " ".join(line.split())


def sweep(files=GUIDANCE_FILES) -> list[tuple[str, int, str, list[str]]]:
    """Every undated negation line naming shipped vocabulary."""
    findings = []
    for rel in files:
        for n, line in enumerate(
            (_REPO / rel).read_text().splitlines(), start=1
        ):
            if not _NEGATION.search(line):
                continue
            if _DATED.search(line):
                # A dated line is an adjudicated capture — the correction
                # convention this estate already runs on.
                continue
            vocab = _vocabulary_in(line)
            if vocab:
                findings.append((rel, n, _normalize(line), vocab))
    return findings


# Adjudicated-true negations, enrolled deliberately. Key: (file, prefix of the
# normalized line). Every entry carries the reason it is TRUE, so the next
# reader adjudicates the reason, not the regex. All twelve founding entries
# were read individually before enrolment (2026-08-10; the notes checker's
# first run measured 1-in-5 precision, so counts are hypotheses until read).
ADJUDICATED: tuple[tuple[str, str, str], ...] = (
    ("skills/mise/SKILL.md", "8. For Google Docs: if `cues.has_suggestions`",
     "advice: don't treat suggested text as settled — not a capability claim"),
    ("skills/mise/SKILL.md", "extraction and never the document itself.",
     "describes the problem raw=True solves; the capability is the next clause"),
    ("skills/mise/SKILL.md", "`comment_reply` / apply edits with `do()`. The API can't *create*",
     "true platform limit: Docs API has no suggestion-creation surface"),
    ("skills/mise/SKILL.md", "other sources don't speak it); `trashed = false`",
     "true design: raw_query is Drive-only by contract"),
    ("skills/mise/SKILL.md", "**Every Doc edit leaves a restore point.**",
     "true platform limit: revision restore/naming is UI-only (mise-cizuzi)"),
    ("skills/mise/SKILL.md", "| Doc has images, tables, or rich formatting | `prepend`/`append`",
     "advice: overwrite destroys rich content — deliberate steering, true"),
    ("skills/mise/SKILL.md", "- **Don't guess the `comment_id`.**",
     "advice: ids come from comments.md — not a capability claim"),
    ("skills/mise/SKILL.md", "Pass CSV as `content` with `doc_type=\"sheet\"`.",
     "advice: trust Drive type detection — not a capability claim"),
    ("skills/mise/SKILL.md", "**`respond` is the one op in this family that ACTS",
     "advice: never RSVP speculatively — deliberate safety steering"),
    ("skills/mise/SKILL.md", "| Separate mark_read operation | Doesn't exist as its own op",
     "true by design: label covers mark_read (generic-primitive rule)"),
    ("skills/mise/SKILL.md", "| Looking for a `mark_read` operation | Doesn't exist",
     "true by design: same fact, anti-patterns table render"),
    ("skills/mise/SKILL.md", "The pattern for all calendaring: the human states intent",
     "advice: never eyeball availability — freebusy IS the capability being taught"),
    ("skills/mise/SKILL.md", "**freebusy's two honesty cues are load-bearing.**",
     "advice: absence-of-location must not read as absence-from-office — honesty steering, true"),
    ("skills/mise/SKILL.md", "**Reading a colleague's diary in detail: `search(calendar_id=",
     "true ACL boundary: a free/busy-only colleague's diary refuses the detail lane "
     "by the OWNER's sharing setting — do(freebusy) still answers (mise-wavotu; "
     "enrolled 2026-08-24, the ship predated this lint run)"),
)


class TestGuidanceFreshness:
    def test_no_unadjudicated_negations(self):
        """A new 'mise can't X' about shipped vocabulary must be adjudicated.

        Red here means one of exactly two things, and both are cheap:
        the claim is STALE (the capability shipped) — fix the guidance; or
        the claim is TRUE (platform limit, design boundary) — enrol it in
        ADJUDICATED with the reason. Never silence the test itself.
        """
        enrolled = {(f, s) for f, s, _ in ADJUDICATED}
        strays = [
            (rel, n, line, vocab)
            for rel, n, line, vocab in sweep()
            if not any(
                rel == f and line.startswith(s) for f, s in enrolled
            )
        ]
        assert not strays, (
            "Negation(s) naming shipped vocabulary, neither dated nor "
            "adjudicated:\n"
            + "\n".join(
                f"  {rel}:{n} [{', '.join(v)}]\n    {line[:140]}"
                for rel, n, line, v in strays
            )
            + "\nIf the capability shipped, fix the guidance. If the claim "
            "is genuinely true, enrol it in ADJUDICATED with its reason."
        )

    def test_adjudicated_entries_still_match(self):
        """An entry matching no current line is residue — prune it.

        Without this, the allowlist rots in the opposite direction: the
        guidance line gets rewritten or deleted and its enrolment lingers,
        ready to silently excuse a future, different negation that happens
        to share the prefix.
        """
        current = sweep()
        stale = [
            (f, s)
            for f, s, _ in ADJUDICATED
            if not any(
                rel == f and line.startswith(s) for rel, n, line, _ in current
            )
        ]
        assert not stale, (
            "ADJUDICATED entries matching no current guidance line "
            f"(rewritten or deleted — remove the enrolment): {stale}"
        )

    def test_synthetic_rot_fires(self, tmp_path):
        """Known-positive control, in the suite forever.

        A checker that reports absence counts only once it has fired on a
        known positive (verification.md). This plants the exact species —
        an undated negation naming a shipped op — and must see it.
        """
        rot = tmp_path / "instructions.md"
        rot.write_text(
            "# Shard\n\nmise cannot create folders — `do(create)` has no "
            "folder support, so hand-roll the Drive API for that.\n"
        )
        findings = _sweep_abs(rot)
        assert findings, "the control line did not fire — the detector is blind"
        assert any("op:create" in f[3] for f in findings)

    def test_dated_corrections_stay_silent(self, tmp_path):
        """A dated line is an adjudicated capture, not fresh rot."""
        doc = tmp_path / "instructions.md"
        doc.write_text(
            "mise couldn't do this until `do(create)` shipped it "
            "(corrected 2026-08-10).\n"
        )
        assert not _sweep_abs(doc)


def _sweep_abs(path: Path) -> list[tuple[str, int, str, list[str]]]:
    """sweep() for an absolute path — test-harness shim for the controls."""
    findings = []
    for n, line in enumerate(path.read_text().splitlines(), start=1):
        if not _NEGATION.search(line) or _DATED.search(line):
            continue
        vocab = _vocabulary_in(line)
        if vocab:
            findings.append((str(path), n, _normalize(line), vocab))
    return findings
