"""
Architectural tests — enforce layer boundaries.

These tests verify that the codebase maintains proper separation of concerns:
- extractors/ must be pure functions with no dependencies on adapters/ or tools/
- adapters/ must not depend on tools/
- tools/ wires everything together

This prevents accidental coupling that would make extractors hard to test.
"""

import ast
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent

# Layers and their forbidden imports. mise_en_space is the facade tier
# (mise-dareti): it sits ABOVE every layer and may import downward freely,
# but nothing may import IT — it appears in every other tier's forbidden
# set so the library contract can never become load-bearing plumbing.
LAYER_RULES = {
    "extractors": {"adapters", "tools", "mise_en_space"},
    "adapters": {"tools", "mise_en_space"},
    # tools can import any layer below it (it's the wiring layer)
    "tools": {"mise_en_space", "server"},
    "workspace": {"adapters", "tools", "extractors", "server", "mise_en_space"},
    "resources": {"extractors", "workspace", "tools", "mise_en_space"},  # may hit adapters (live state), not business logic
    "mise_en_space": {"server", "resources"},  # the facade fronts the library, not the MCP shim
}

# Files outside any layer directory — the tier the 2026-06-10 toise flagged:
# mass accumulates exactly where LAYER_RULES can't see (server.py hit 1,318
# lines before mise-jimohe). Rules are DISCOVERED, not enumerated: every root
# *.py gets the strict default automatically, so adding a file cannot quietly
# open a new unpoliced tier. Root utilities sit BELOW the layers and may not
# import upward (root→root imports like auth→token_store are fine).
_ROOT_DEFAULT_FORBIDDEN = {"adapters", "tools", "workspace", "extractors", "server", "resources", "mise_en_space"}

# Entry points and documented exceptions:
# - server.py / cli.py reach DOWN into tools (registration/wiring) — never
#   into extraction or workspace internals; server.py may also touch adapters
#   (lifespan housekeeping).
# - retry.py imports adapters.http_client (clear_sync_client for auth-refresh
#   retry) — the one sanctioned root→adapters import.
_ROOT_OVERRIDES = {
    "server.py": {"extractors", "workspace", "mise_en_space"},
    "cli.py": {"adapters", "extractors", "workspace", "server", "resources", "mise_en_space"},
    "retry.py": {"tools", "workspace", "extractors", "server", "resources", "mise_en_space"},
}

FILE_RULES = {
    path.name: _ROOT_OVERRIDES.get(path.name, _ROOT_DEFAULT_FORBIDDEN)
    for path in sorted(PROJECT_ROOT.glob("*.py"))
}

# server.py is the registration shim — tools/resources own the logic. If this
# trips, move the new code into tools/ or resources/ (see mise-jimohe; it was
# 1,318 lines when CLAUDE.md still claimed it "just registers tools").
SERVER_MAX_LINES = 500

# Every module under the layer directories gets the SAME number, so there is one
# standard in the repo rather than a special case for server.py (mise-nebewe).
MODULE_MAX_LINES = 500
POLICED_DIRS = ("extractors", "adapters", "tools", "workspace", "resources", "mise_en_space")

# Grandfathered debt, measured 2026-08-03. These eleven modules were already over
# the cap when it was extended repo-wide, and splitting them is deliberately NOT
# part of that change — it would make the diff unreviewable. So they are frozen at
# the size they were, and may only come DOWN.
#
# Why a ratchet and not a flat cap: the two alternatives were splitting eleven
# modules at once, or granting eleven open-ended exemptions. The first is separate
# work; the second decays into permission. A ratchet needs neither — existing mass
# is pinned, new growth is refused, and the numbers only fall.
#
# Why this is not the enumeration trap it looks like: what is enumerated here is
# MEASUREMENTS, not rules. Any module absent from this dict — including every
# module added in future — is governed by MODULE_MAX_LINES via the glob, so
# discovery still decides who is policed. This dict only records who already owed.
_LEGACY_SIZE_BASELINE = {
    "adapters/drive.py": 1151,
    "adapters/gmail.py": 1066,  # tightened 2026-08-07: id resolvers split to gmail_ids.py
    "tools/create.py": 922,  # tightened 2026-08-09 thrice: find_placeholder_indices moved to doc_chips.py, de-aliased imports (mise-rafote), csv_text_to_values moved to extractors/sheets.py (mise-kacani)
    "extractors/docs.py": 892,
    "tools/fetch/drive.py": 805,  # tightened 2026-08-08: _write_per_tab_csvs moved to common.py (mise-dogape)
    "resources/docs.py": 851,  # +30 for the 'people' search source (mise-mahiho); the last 6 are the multi-word query trap, added after a live probe showed `orgTitle:Head of Strategy` returns zero SILENTLY — a caller who doesn't know that reads the zero as "nobody has that job" — this module's mass IS resource text, so its ceiling tracks CAPABILITY additions; splitting a resource string across siblings would be worse. Raise only with a new op or search source, and only by what the new capability's grammar actually needs. The people entry earns its lines on query grammar (Admin SDK syntax, not Drive's) plus two honesty notes the caller cannot infer.
    "tools/fetch/gmail.py": 703,  # +1 (2026-08-07): the gmail_ids split turned one import into two; +6 (2026-08-09): web_link emission, logic lives in gmail_ids.thread_web_link_or_warn (mise-hetaba); +2 (2026-08-13): thumbnails opt-out — one signature line, one if-guard at the render site (mise-giwawa)
    "adapters/http_client.py": 703,  # +11 (2026-08-12): ambient-mode dispatch — a 4-line branch in _load_and_diagnose_credentials plus a 5-line refresh guard in EACH near-duplicate client (mise-wasagu). +9 (2026-08-12 evening, mise-dareti): constructor-injected credentials pay the SAME three seams — a 3-line return in the loader, a 3-line refusal in each refresh path; the registry and teaching text live in token_store. These are the only seams identity selection can intercept. Halves when MiseSyncClient dies in Phase 2.
    "extractors/slides.py": 600,
    "adapters/pdf.py": 509,  # lowered 2026-08-08: flattened-table detector moved to extractors/text_quality.py (mise-columi); +5 (2026-08-13): thumbnails opt-out — two signature lines, one docstring line, two if-guards at the render sites; the gating must sit beside the renders it skips (mise-giwawa)
    "extractors/talon_signature.py": 518,
}

# A real shrink should tighten the ratchet, or the baseline silently becomes
# permission to grow back — adapters/gmail.py could fall to 600 and return to
# 1113 unobserved. But demanding bookkeeping for every three-line edit would breed
# ignoring the test, so ordinary churn is tolerated and only a substantial shrink
# is banked.
_SHRINK_TOLERANCE = 50


def get_imports_from_file(filepath: Path) -> set[str]:
    """Extract all import names from a Python file."""
    try:
        with open(filepath) as f:
            tree = ast.parse(f.read(), filename=str(filepath))
    except SyntaxError:
        return set()

    imports = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])

    return imports


def get_python_files(directory: Path) -> list[Path]:
    """Get all Python files in a directory (recursive to cover subpackages like tools/fetch/)."""
    if not directory.exists():
        return []
    return list(directory.rglob("*.py"))


class TestLayerBoundaries:
    """Verify that layer boundaries are respected."""

    @pytest.mark.parametrize("layer,forbidden", list(LAYER_RULES.items()))
    def test_layer_does_not_import_forbidden(self, layer: str, forbidden: set[str]) -> None:
        """Each layer must not import from its forbidden layers."""
        layer_dir = PROJECT_ROOT / layer
        violations = []

        for filepath in get_python_files(layer_dir):
            imports = get_imports_from_file(filepath)
            bad_imports = imports & forbidden

            if bad_imports:
                rel_path = filepath.relative_to(PROJECT_ROOT)
                violations.append(
                    f"{rel_path} imports {bad_imports}"
                )

        assert not violations, (
            f"Layer '{layer}' has forbidden imports:\n" +
            "\n".join(f"  - {v}" for v in violations)
        )

    def test_extractors_are_pure(self) -> None:
        """
        Extractors must only import from stdlib, shared models, and extraction utilities.

        This ensures layer isolation: extractors don't import from adapters or tools.
        Third-party extraction utilities (markitdown, regex) are allowed since they
        transform data without making API calls.
        """
        extractors_dir = PROJECT_ROOT / "extractors"
        allowed_stdlib = {
            # Common stdlib modules extractors might need
            "typing", "re", "json", "datetime", "collections",
            "itertools", "functools", "dataclasses", "enum",
            "html", "xml", "csv", "io", "textwrap", "string",
            "base64", "tempfile", "os", "logging",
            # The package itself (internal imports)
            "extractors", "talon_signature",
            # Shared type definitions (allowed - no side effects)
            "models",
            # Extraction utilities (no API calls, just data transformation)
            "markitdown",  # HTML/PDF/Office → markdown conversion
            "regex",       # Enhanced regex (talon needs duplicate named groups)
            "PIL",          # Image validation (no API calls, pure byte inspection)
            "html_convert", # Shared HTML cleaning (clean_html_for_conversion is pure)
        }

        violations = []

        for filepath in get_python_files(extractors_dir):
            if filepath.name == "__init__.py":
                continue

            imports = get_imports_from_file(filepath)
            # Filter out stdlib (approximation: anything in sys.stdlib_module_names if available)
            stdlib_modules = getattr(sys, "stdlib_module_names", set())
            non_stdlib = imports - stdlib_modules - allowed_stdlib

            if non_stdlib:
                rel_path = filepath.relative_to(PROJECT_ROOT)
                violations.append(
                    f"{rel_path} imports non-stdlib: {non_stdlib}"
                )

        assert not violations, (
            f"Extractors must be pure (stdlib only):\n" +
            "\n".join(f"  - {v}" for v in violations)
        )


class TestFileBoundaries:
    """Layer discipline for files that live outside the layer directories.

    Companion to TestLayerBoundaries — same AST mechanism, per-file rules.
    Covers server.py and the shared root utilities, the tier the layered
    rules cannot see.
    """

    @pytest.mark.parametrize("filename,forbidden", list(FILE_RULES.items()))
    def test_file_does_not_import_forbidden(self, filename: str, forbidden: set[str]) -> None:
        filepath = PROJECT_ROOT / filename
        assert filepath.exists(), f"{filename} missing — update FILE_RULES if it moved"
        imports = get_imports_from_file(filepath)
        bad_imports = imports & forbidden
        assert not bad_imports, (
            f"{filename} imports {bad_imports} — it sits "
            f"{'above tools (entry point)' if filename in ('server.py', 'cli.py') else 'below the layers (shared utility)'}; "
            f"move the logic, don't widen the rule"
        )

    def test_server_stays_thin(self) -> None:
        """server.py is registration + thin wrappers. Logic lives in tools/
        and resources/. The cap exists because the entry point quietly grew
        to 1,318 lines while CLAUDE.md said it 'just registers tools'."""
        server_path = PROJECT_ROOT / "server.py"
        line_count = len(server_path.read_text().splitlines())
        assert line_count <= SERVER_MAX_LINES, (
            f"server.py is {line_count} lines (cap {SERVER_MAX_LINES}). "
            f"Move new logic into tools/ or resources/ — see mise-jimohe."
        )


class TestPackageStructure:
    """Verify expected package structure exists."""

    @pytest.mark.parametrize("package", ["extractors", "adapters", "tools", "workspace"])
    def test_package_has_init(self, package: str) -> None:
        """Each package must have an __init__.py."""
        init_file = PROJECT_ROOT / package / "__init__.py"
        assert init_file.exists(), f"{package}/__init__.py missing"

    def test_fixtures_is_not_package(self) -> None:
        """fixtures/ should be data directory, not a Python package."""
        init_file = PROJECT_ROOT / "fixtures" / "__init__.py"
        assert not init_file.exists(), (
            "fixtures/__init__.py should not exist — it's a data directory"
        )


class TestModuleSize:
    """
    Every module under the layer directories is size-policed, not just server.py.

    Why this exists (mise-nebewe). understanding.md's lead section argues that mass
    accumulates exactly where mechanical enforcement cannot see, and cites the
    jimohe shrink — server.py 1,318 → 424 — as the fix. But SERVER_MAX_LINES capped
    server.py ALONE, so server.py was the only file in the repo actually held to it
    while eleven modules under tools/, adapters/, extractors/ and resources/ sat
    over the same number, the largest at 1,151. The lesson the repo draws about
    itself is that the remedy is extending the RULE, not cleaning the module:
    cleaning without a rule resets a counter nothing watches.

    The growth is not hypothetical. Between the item being filed on 2026-08-01 and
    this test being written on 2026-08-03, adapters/gmail.py went 1059 → 1113 and
    tools/fetch/gmail.py went 655 → 694 — 93 lines in two days, in the modules the
    previous session had worked in, with nothing to notice.

    If this test trips on a module you are growing, the answer is to move code into
    a sibling rather than to raise the number. Raising a baseline entry is a
    deliberate act that should be argued for in the commit message.
    """

    @staticmethod
    def _line_count(path: Path) -> int:
        return len(path.read_text().splitlines())

    def _policed_modules(self) -> list[Path]:
        found: list[Path] = []
        for directory in POLICED_DIRS:
            found.extend(sorted((PROJECT_ROOT / directory).rglob("*.py")))
        return found

    def test_baseline_entries_are_all_above_the_default(self) -> None:
        """
        A baseline entry at or below MODULE_MAX_LINES is dead weight — the default
        already covers it, and leaving it in place would GRANT room rather than
        remove it. This asserts the config is well-formed, because a ratchet whose
        own bookkeeping is never checked is the next silent gap.
        """
        redundant = {
            name: cap for name, cap in _LEGACY_SIZE_BASELINE.items()
            if cap <= MODULE_MAX_LINES
        }
        assert not redundant, (
            "These baseline entries are at or below the default cap of "
            f"{MODULE_MAX_LINES} and should be DELETED from _LEGACY_SIZE_BASELINE "
            f"— they now grant room instead of removing it: {redundant}"
        )

    def test_baseline_names_real_files(self) -> None:
        """A baseline entry for a file that no longer exists is stale permission."""
        missing = [
            name for name in _LEGACY_SIZE_BASELINE
            if not (PROJECT_ROOT / name).exists()
        ]
        assert not missing, (
            "_LEGACY_SIZE_BASELINE names files that no longer exist — delete these "
            f"entries: {missing}"
        )

    def test_no_module_exceeds_its_ceiling(self) -> None:
        """No module may grow past its cap — the default, or its baselined size."""
        violations = []

        for path in self._policed_modules():
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            ceiling = _LEGACY_SIZE_BASELINE.get(rel, MODULE_MAX_LINES)
            actual = self._line_count(path)
            if actual > ceiling:
                grandfathered = rel in _LEGACY_SIZE_BASELINE
                how = (
                    f"baselined at {ceiling}" if grandfathered
                    else f"default cap {MODULE_MAX_LINES}"
                )
                violations.append(f"{rel}: {actual} lines, {how}")

        assert not violations, (
            "Modules over their size ceiling:\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nMove code into a sibling module rather than raising the number. "
            "server.py hit 1,318 lines this way while CLAUDE.md still claimed it "
            '"just registers tools" (mise-jimohe).'
        )

    def test_shrunk_modules_tighten_the_ratchet(self) -> None:
        """
        A module that has genuinely shrunk must have its baseline lowered, or the
        entry becomes permission to grow back. Tolerance is _SHRINK_TOLERANCE lines
        so ordinary editing does not demand bookkeeping.
        """
        stale = []

        for rel, recorded in sorted(_LEGACY_SIZE_BASELINE.items()):
            path = PROJECT_ROOT / rel
            if not path.exists():
                continue  # covered by test_baseline_names_real_files
            actual = self._line_count(path)
            if actual < recorded - _SHRINK_TOLERANCE:
                stale.append(f'    "{rel}": {actual},  # was {recorded}')

        assert not stale, (
            "These modules have shrunk by more than "
            f"{_SHRINK_TOLERANCE} lines — bank the win by lowering their entries in "
            "_LEGACY_SIZE_BASELINE, or the ratchet lets them grow back:\n"
            + "\n".join(stale)
        )


class TestProbeScriptsSeeTheWorkingTree:
    """
    A script under scripts/ that imports a root-tier module must put the repo
    root on sys.path FIRST — otherwise it silently imports a STALE COPY.

    Why (measured 2026-08-03, mise-fogede). pyproject's
    [tool.hatch.build.targets.wheel.force-include] lists the root-tier modules so
    the built wheel is complete for library consumers (glaneur). force-include has
    no notion of a source mapping, so it copies those files PHYSICALLY even into
    the editable install, while the layer directories ride the .pth and always
    resolve live. site-packages precedes the .pth entry in sys.path, so the copies
    WIN for any process whose sys.path[0] is not the repo root — and `uv sync`
    does not refresh them (it reports the editable install satisfied and rebuilds
    nothing; only `uv sync --reinstall-package mise-en-space` does). Measured
    once at 222 changed lines behind, missing the two functions the session had
    just shipped.

    Three execution modes, measured with real script files rather than emulated:
    a script at the repo root resolves LIVE (which is the only reason
    smoke_stdio.py's spawned server.py is honest); a script under scripts/ and a
    script in /tmp both resolve to the STALE COPY.

    This rule is PURELY preventive, and honestly so: as of 2026-08-04 no script
    under scripts/ imports a root-tier module at all, so the violating branch is
    not exercised by any current file. It codified a convention that
    scripts/capture_fixtures.py had followed correctly since March — that script
    was deleted the same day (mise-sowepo, it had been broken since the httpx
    migration), which is what left this test without a live worked example. The
    controls are therefore the only evidence it works: see the commit that added
    it, where an unfixed script reddens it by name and moving a path fix to after
    its import reddens it with "sys.path fixed too late".

    Keep it anyway. The failure mode is silent and it lands in the repo's
    most-used instrument, since the whole methodology here is probe-before-
    building — smoke_stdio.py is one `from validation import …` away from it.
    """

    # Discovered, not enumerated — same principle as FILE_RULES above, so adding
    # a root module cannot quietly fall outside the rule.
    ROOT_MODULES = {path.stem for path in PROJECT_ROOT.glob("*.py")}

    REMEDY = (
        "Put the repo root on sys.path before the import:\n"
        "    import sys\n"
        "    from pathlib import Path\n"
        "    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n"
        "…or move the script to the repo root, where sys.path[0] already is the "
        "repo. See mise-fogede and .bon/understanding.md "
        '"Which code does your probe actually reach?".'
    )

    @staticmethod
    def _first_sys_path_mutation(tree: ast.AST) -> int | None:
        """Line of the first sys.path.insert/append call, or None."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in {"insert", "append"}
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "path"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "sys"
            ):
                return node.lineno
        return None

    def _root_imports(self, tree: ast.AST) -> list[tuple[str, int]]:
        """(module, lineno) for every import of a root-tier module."""
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in self.ROOT_MODULES:
                        found.append((top, node.lineno))
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                top = node.module.split(".")[0]
                if top in self.ROOT_MODULES:
                    found.append((top, node.lineno))
        return found

    def test_scripts_importing_root_modules_fix_sys_path_first(self) -> None:
        scripts_dir = PROJECT_ROOT / "scripts"
        violations = []

        for filepath in sorted(scripts_dir.glob("*.py")):
            try:
                tree = ast.parse(filepath.read_text(), filename=str(filepath))
            except SyntaxError:
                continue

            root_imports = self._root_imports(tree)
            if not root_imports:
                continue

            fix_line = self._first_sys_path_mutation(tree)
            for module, lineno in root_imports:
                if fix_line is None or fix_line >= lineno:
                    where = "no sys.path fix at all" if fix_line is None else (
                        f"sys.path fixed too late, at line {fix_line}"
                    )
                    violations.append(
                        f"{filepath.relative_to(PROJECT_ROOT)}:{lineno} "
                        f"imports root module '{module}' — {where}"
                    )

        assert not violations, (
            "These scripts would import STALE copies from .venv/…/site-packages, "
            "not the working tree:\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\n"
            + self.REMEDY
        )
