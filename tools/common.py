"""Shared utilities for tool implementations."""

from pathlib import Path


def resolve_source(source: str | None, base_path: str | None) -> Path | None:
    """Resolve source path relative to base_path.

    Returns None if no source. Raises ValueError if source given without base_path
    or if the resolved path escapes base_path (path traversal).
    """
    if not source:
        return None
    if not base_path:
        raise ValueError("base_path is required when using source — pass your working directory")
    source_path = Path(source)
    if source_path.is_absolute():
        resolved = source_path
    else:
        resolved = Path(base_path) / source_path
    # Containment check: resolved path must stay within base_path
    try:
        resolved_real = resolved.resolve()
        base_real = Path(base_path).resolve()
        if not str(resolved_real).startswith(str(base_real) + "/") and resolved_real != base_real:
            raise ValueError(
                f"source path '{source}' resolves outside working directory"
            )
    except OSError as e:
        raise ValueError(f"Cannot resolve source path '{source}': {e}") from e
    return resolved
