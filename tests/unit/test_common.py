"""Tests for tools/common.py — shared utilities."""

from pathlib import Path

import pytest

from tools.common import resolve_source


class TestResolveSource:
    """Test source path resolution and containment."""

    def test_none_returns_none(self) -> None:
        assert resolve_source(None, "/some/path") is None

    def test_empty_returns_none(self) -> None:
        assert resolve_source("", "/some/path") is None

    def test_missing_base_path_raises(self) -> None:
        with pytest.raises(ValueError, match="base_path is required"):
            resolve_source("mise/something", None)

    def test_relative_path_resolved(self, tmp_path: Path) -> None:
        deposit = tmp_path / "mise" / "doc--test--abc123"
        deposit.mkdir(parents=True)
        result = resolve_source("mise/doc--test--abc123", str(tmp_path))
        assert result == deposit

    def test_rejects_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="outside working directory"):
            resolve_source("../../../etc", str(tmp_path))

    def test_rejects_absolute_outside(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="outside working directory"):
            resolve_source("/tmp", str(tmp_path))

    def test_base_path_itself_allowed(self, tmp_path: Path) -> None:
        # source="." should resolve to base_path itself
        result = resolve_source(".", str(tmp_path))
        assert result is not None
