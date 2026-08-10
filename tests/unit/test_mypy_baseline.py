"""Mypy ratchet: the documented type-check must be able to report a NEW error.

The rot species (mise-bunuvu): a standing error count turns the dev command
into wallpaper. CLAUDE.md documented "16 errors" the day the tree held 18;
the count moved 16→18→20 across a week — twice WITHOUT the named files
changing (extractors/image.py untouched since 2026-03-02; stub tightening
suspected, unproven) and twice via new code (adapters/people.py shipped two
in 1.49.0 that sat uncounted in prose for a day). Every reader had to
hand-diff counts against a stale paragraph to learn whether an error was
theirs. An unpoliced check is not a check.

DECISION (recorded per the brief's step 3): BASELINE, not fix-to-zero.
Errors here arrive from environment churn as well as code churn, and a zero
target re-breaks on the next stub bump through nobody's fault — the only
"fix" for upstream stub noise being `# type: ignore`, the invisible-debt
move the brief forbids. A per-(file, error-code) baseline absorbs that
churn as a deliberate, reasoned enrolment instead. CI wiring is free: CI
already installs mypy (--all-groups) and only ever runs pytest, so this
test IS the CI mypy gate. Route (a) — casting away the http_client
no-any-return cluster — stays open as separate work; executing it trips
the down-ratchet below, which forces the baseline lower. The routes
compose; nothing is foreclosed.

The baseline is a ratchet, not an exemption (the _LEGACY_SIZE_BASELINE /
test_guidance_freshness.py ADJUDICATED pattern): a NEW (file, code) pair or
a count above baseline fails with the verbatim mypy lines; a count BELOW
baseline also fails, demanding the entry be lowered, so fixed debt cannot
silently grow back. Raising an entry is a deliberate act to argue in the
commit message.

Scope is the documented command's, verbatim: models.py extractors/
adapters/ validation.py workspace/. tools/, resources/ and server.py sit
outside the documented scope — a pre-existing fact, not this test's
decision; widening is its own adjudication.

Cost: ~0.5s warm (.mypy_cache), ~12s cold (what CI pays). A missing mypy
fails loudly rather than skipping — a skip would silently void the guard
in exactly the environment (CI) it exists for.
"""

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

_REPO = Path(__file__).parents[2]

# The documented command's targets, verbatim (CLAUDE.md → Development).
MYPY_TARGETS = (
    "models.py",
    "extractors/",
    "adapters/",
    "validation.py",
    "workspace/",
)

# path:line: error: message  [code]
_ERROR_LINE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+): error: (?P<msg>.*?)\s+\[(?P<code>[a-z0-9-]+)\]$"
)

# Adjudicated standing debt. Key: (file, mypy error code) → (count, reason).
# Every entry was read individually before enrolment (2026-08-10). The next
# reader adjudicates the REASON, not the number.
MYPY_BASELINE: dict[tuple[str, str], tuple[int, str]] = {
    ("adapters/http_client.py", "no-any-return"): (
        11,
        "the get_json family returns response.json(), which is Any; "
        "mise-bunuvu route (a) — cast() or a typed wrapper — closes these "
        "and is deliberately separate work",
    ),
    ("adapters/http_client.py", "arg-type"): (
        2,
        "httpx's params union is narrower than our dict[str, Any] at the "
        "two stream() call sites; wants a typed alias at the seam",
    ),
    ("adapters/http_client.py", "no-untyped-call"): (
        1,
        "google-auth's Credentials.from_authorized_user_info is untyped "
        "upstream",
    ),
    ("adapters/conversion.py", "no-any-return"): (
        2,
        "Any escaping an untyped Google response into str-declared returns "
        "— same family as http_client",
    ),
    ("adapters/people.py", "no-any-return"): (
        2,
        "same response.json() family; shipped in 1.49.0 and sat uncounted "
        "in prose for a day — the data point that argued for this ratchet",
    ),
    ("extractors/image.py", "assignment"): (
        1,
        "stubs-only, not runtime: the resize path is test-covered and "
        "Image.LANCZOS resolves to 1 on Pillow 12.3.0; appeared with the "
        "file unchanged since 2026-03 (stub tightening suspected, unproven)",
    ),
    ("extractors/image.py", "attr-defined"): (
        1,
        "same stubs-only pair as the assignment error above",
    ),
}


@pytest.fixture(scope="module")
def mypy_findings() -> tuple[Counter, dict[tuple[str, str], list[str]]]:
    """Run the documented mypy command once; aggregate per (file, code).

    Returns the counts and the verbatim lines behind each key, so a failure
    can name the actual error rather than a number.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            *MYPY_TARGETS,
            "--no-error-summary",
            "--no-color-output",
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=300,
    )
    # 0 = clean, 1 = errors found; anything else is the instrument failing
    # (bad flags, missing mypy, internal error) — never a finding.
    if proc.returncode not in (0, 1):
        pytest.fail(
            f"mypy did not run (exit {proc.returncode}) — instrument "
            f"failure, not a type-check result:\n{proc.stdout}\n{proc.stderr}"
        )
    counts: Counter = Counter()
    lines_by_key: dict[tuple[str, str], list[str]] = {}
    for line in proc.stdout.splitlines():
        m = _ERROR_LINE.match(line)
        if m:
            key = (m["file"], m["code"])
            counts[key] += 1
            lines_by_key.setdefault(key, []).append(line)
    if proc.returncode == 1 and not counts:
        pytest.fail(
            "mypy reported errors (exit 1) but the parser matched none — "
            "the output format has moved and this guard is blind:\n"
            + proc.stdout[:2000]
        )
    return counts, lines_by_key


class TestMypyRatchet:
    def test_no_new_mypy_errors(self, mypy_findings):
        """A type error above baseline is YOURS until adjudicated.

        Fix it, or — if it is genuinely standing debt (upstream stub churn,
        an untyped third-party surface) — add/raise the (file, code) entry
        in MYPY_BASELINE with its reason, and argue the raise in the commit
        message. Never silence this test itself.
        """
        counts, lines_by_key = mypy_findings
        new = {
            key: lines_by_key[key]
            for key, n in counts.items()
            if n > MYPY_BASELINE.get(key, (0, ""))[0]
        }
        assert not new, (
            "NEW mypy error(s) above the recorded baseline:\n"
            + "\n".join(
                f"  {file} [{code}] — {n} found, "
                f"{MYPY_BASELINE.get((file, code), (0, ''))[0]} baselined:\n"
                + "\n".join(f"    {ln}" for ln in lines_by_key[(file, code)])
                for (file, code), n in sorted(counts.items())
                if n > MYPY_BASELINE.get((file, code), (0, ""))[0]
            )
            + "\nFix them, or enrol/raise the MYPY_BASELINE entry with a "
            "reason and argue it in the commit message."
        )

    def test_no_stale_baseline_entries(self, mypy_findings):
        """Fixed debt must lower the baseline, so it cannot grow back.

        Without this direction the baseline rots into headroom: someone
        fixes three errors, the entry stays, and three future regressions
        arrive invisibly under the old number.
        """
        counts, _ = mypy_findings
        stale = {
            key: (expected, counts.get(key, 0))
            for key, (expected, _reason) in MYPY_BASELINE.items()
            if counts.get(key, 0) < expected
        }
        assert not stale, (
            "MYPY_BASELINE entries above the tree's actual state "
            "(errors were fixed — bank the win):\n"
            + "\n".join(
                f"  {file} [{code}]: baselined {exp}, observed {obs} — "
                f"lower the entry to {obs}"
                + (" (delete it)" if obs == 0 else "")
                for (file, code), (exp, obs) in sorted(stale.items())
            )
        )

    def test_parser_fires_on_known_positive(self):
        """A parser that reports absence counts only once it has fired.

        Canned line in the real format; if mypy's output shape drifts, the
        exit-1-but-zero-parsed guard in the fixture catches the live case,
        and this catches parser edits directly.
        """
        line = (
            "adapters/http_client.py:269: error: Returning Any from "
            'function declared to return "dict[str, Any]"  [no-any-return]'
        )
        m = _ERROR_LINE.match(line)
        assert m
        assert m["file"] == "adapters/http_client.py"
        assert m["code"] == "no-any-return"
        assert not _ERROR_LINE.match(
            "adapters/http_client.py:269: note: See upstream docs"
        ), "notes must not count as errors"
