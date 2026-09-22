"""What a folder create ignores, said out loud (mise-tijeko).

A folder holds no content, so content=/source=/file_path= and the Docs-only
page_setup= have nothing to act on. They used to vanish in silence. Put to
blank-slate callers, 5 of 8 wanted the folder made WITH a warning (3 wanted a
refusal; nobody wanted silence to go on, and 7 of 8 would distrust a tool that
quietly minted a doc inside the folder for them) — so the folder is created
and the result says what was ignored and where the text could go instead.
"""

from typing import Any

from models import DoResult


def warn_folder_ignored(
    result: DoResult | dict[str, Any],
    *,
    content: str | None,
    source: str | None,
    file_path: str | None,
    page_setup: str | None,
) -> DoResult | dict[str, Any]:
    """Append an ignored-params warning to a successful folder create."""
    if not isinstance(result, DoResult):
        return result
    warnings: list[str] = []
    body = [name for name, value in
            (("content", content), ("source", source), ("file_path", file_path)) if value]
    if body:
        warnings.append(
            f"{', '.join(n + '=' for n in body)} ignored — a folder holds no "
            f"content. To put it in a doc inside this folder: do(create, "
            f"doc_type='doc', folder_id='{result.file_id}', …).")
    if page_setup:
        warnings.append("page_setup= applies only to doc_type='doc' — ignored for this folder.")
    if warnings:
        result.cues.setdefault("warnings", []).extend(warnings)
    return result
