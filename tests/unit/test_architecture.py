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

# Layers and their forbidden imports
LAYER_RULES = {
    "extractors": {"adapters", "tools"},  # extractors can't import adapters or tools
    "adapters": {"tools"},                 # adapters can't import tools
    # tools can import anything (it's the wiring layer)
    "workspace": {"adapters", "tools", "extractors", "server"},  # pure folder management
    "resources": {"extractors", "workspace", "tools"},  # resources may hit adapters (live state) but not business logic
}

# Files outside any layer directory — the tier the 2026-06-10 toise flagged:
# mass accumulates exactly where LAYER_RULES can't see (server.py hit 1,318
# lines before mise-jimohe). Rules are DISCOVERED, not enumerated: every root
# *.py gets the strict default automatically, so adding a file cannot quietly
# open a new unpoliced tier. Root utilities sit BELOW the layers and may not
# import upward (root→root imports like auth→token_store are fine).
_ROOT_DEFAULT_FORBIDDEN = {"adapters", "tools", "workspace", "extractors", "server", "resources"}

# Entry points and documented exceptions:
# - server.py / cli.py reach DOWN into tools (registration/wiring) — never
#   into extraction or workspace internals; server.py may also touch adapters
#   (lifespan housekeeping).
# - retry.py imports adapters.http_client (clear_sync_client for auth-refresh
#   retry) — the one sanctioned root→adapters import.
_ROOT_OVERRIDES = {
    "server.py": {"extractors", "workspace"},
    "cli.py": {"adapters", "extractors", "workspace", "server", "resources"},
    "retry.py": {"tools", "workspace", "extractors", "server", "resources"},
}

FILE_RULES = {
    path.name: _ROOT_OVERRIDES.get(path.name, _ROOT_DEFAULT_FORBIDDEN)
    for path in sorted(PROJECT_ROOT.glob("*.py"))
}

# server.py is the registration shim — tools/resources own the logic. If this
# trips, move the new code into tools/ or resources/ (see mise-jimohe; it was
# 1,318 lines when CLAUDE.md still claimed it "just registers tools").
SERVER_MAX_LINES = 500


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

    This rule is preventive — scripts/capture_fixtures.py already does the right
    thing at its lines 18-19 and nothing enforced it. The failure mode is silent
    and lands in the repo's most-used instrument, since the whole methodology
    here is probe-before-building.
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
