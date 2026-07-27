"""Shared utilities for tool implementations."""

from pathlib import Path


# A replace_text that matched nothing returns the same success-shaped payload as
# one that worked, so the wording is the only thing standing between a caller and
# a silent no-op (mise-nacolu — an external-facing doc went unedited for a day).
# Says what happened to the DOCUMENT, not merely that a string was absent; shared
# by all three replace_text paths (docs, sheets, plain files) so they can't drift.
NO_MATCH_WARNING = "NO CHANGE — find matched 0 times; document not modified"

# Markers that a fetched Doc's *rendering* carries but the document itself does
# not: markdown emphasis and code from content.md, the checkbox export's ~~, and
# CriticMarkup from suggestions='markup'. Copy a span out of a deposit including
# one of these and replace_text can never match. Deliberately excludes lone * and
# _ — both are common in real prose and filenames, and a hint that cries wolf
# stops being read. Two-character sequences only; near-certainly artefacts.
_RENDERING_MARKERS = ("**", "__", "~~", "`", "{++", "{--")


def markdown_marker_hint(find: str) -> str:
    """Diagnose a zero-match find string that carries rendering artefacts.

    Returns a sentence to append to NO_MATCH_WARNING, or '' when nothing looks
    like a rendering artefact.

    Deliberately a *diagnosis on an already-failing call*, not a pre-flight gate.
    A gate that inspects only the ends of `find` would miss the common shape —
    emphasis in the middle of a copied sentence ('the **bold** bit') — and would
    hand back false confidence that the string had been vetted. Firing only
    alongside a NO CHANGE warning means a wrong guess costs a bad suggestion,
    never a bad outcome.
    """
    found = [m for m in _RENDERING_MARKERS if m in find]
    if not found:
        return ""
    return (
        f" — `find` contains {', '.join(found)}, which is formatting in fetch's "
        "rendering, not text in the document. Search the plain words instead."
    )


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
