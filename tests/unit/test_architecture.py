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
}


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
    """Get all Python files in a directory (non-recursive for top-level packages)."""
    if not directory.exists():
        return []
    return list(directory.glob("*.py"))


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
                violations.append(
                    f"{filepath.name} imports {bad_imports}"
                )

        assert not violations, (
            f"Layer '{layer}' has forbidden imports:\n" +
            "\n".join(f"  - {v}" for v in violations)
        )

    def test_extractors_are_pure(self) -> None:
        """
        Extractors must only import from stdlib, typing, and shared models.

        This ensures they're truly pure functions with no side effects
        or external dependencies that would make testing difficult.
        """
        extractors_dir = PROJECT_ROOT / "extractors"
        allowed_stdlib = {
            # Common stdlib modules extractors might need
            "typing", "re", "json", "datetime", "collections",
            "itertools", "functools", "dataclasses", "enum",
            "html", "xml", "csv", "io", "textwrap", "string",
            # The package itself
            "extractors",
            # Shared type definitions (allowed - no side effects)
            "models",
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
                violations.append(
                    f"{filepath.name} imports non-stdlib: {non_stdlib}"
                )

        assert not violations, (
            f"Extractors must be pure (stdlib only):\n" +
            "\n".join(f"  - {v}" for v in violations)
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
