"""Shared utilities for tool implementations."""

from pathlib import Path


def resolve_source(source: str | None, base_path: str | None) -> Path | None:
    """Resolve source path relative to base_path.

    Returns None if no source. Raises ValueError if source given without base_path.
    """
    if not source:
        return None
    if not base_path:
        raise ValueError("base_path is required when using source — pass your working directory")
    source_path = Path(source)
    return source_path if source_path.is_absolute() else Path(base_path) / source_path
