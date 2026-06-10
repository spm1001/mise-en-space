"""
Remote-mode orchestration — the post-hoc read-back pattern.

Remote mode (StreamableHTTP for Claude.ai connectors) never touches the
fetchers: do_search/do_fetch run unchanged against a temp dir, then these
wrappers read the deposited content back and inline it in the response.
Keep the interception HERE, never inside the fetchers (see understanding.md
"Remote mode architecture").

server.py owns the _REMOTE_MODE flag (it must exist before @mcp.tool
decorators run); this module owns what remote mode DOES.
"""

import shutil
import tempfile
from pathlib import Path
from typing import Any

from models import FetchResult
from tools.fetch.router import do_fetch
from tools.search import do_search

# Operations allowed in remote mode — everything else is rejected.
# Criteria: reversible, non-destructive, doesn't expose files to others.
# Audit (Mar 2026): create is the broadest — doc_type='file' can write arbitrary
# content to any folder the token can access. Acceptable for single-user (your own
# Drive); reconsider if ever multi-tenant. draft/reply_draft are safe (drafts, not
# sent). archive/star/label are metadata-only. Excluded: move, rename, share (exposes
# files), overwrite/prepend/append/replace_text (destructive content changes).
REMOTE_ALLOWED_OPS = {"create", "draft", "reply_draft", "archive", "star", "label"}


def search_remote(
    query: str, sources: list[str] | None, max_results: int,
    base_path: str, folder_id: str | None, type: str | None = None,
) -> dict[str, Any]:
    """
    Remote search: deposit to temp dir, return full results inline.

    SearchResult.to_dict() already returns full results when path is None
    (legacy/inline mode). We use a temp dir for the deposit, then return
    full_results() directly without setting path.
    """
    if base_path:
        effective_base = Path(base_path)
        temp_dir = None
    else:
        temp_dir = tempfile.mkdtemp(prefix="mise-remote-search-")
        effective_base = Path(temp_dir)
    try:
        result = do_search(query, sources, max_results, base_path=effective_base, folder_id=folder_id, type=type)
        # Strip the path — remote clients can't read it. This triggers
        # SearchResult.to_dict() to return full results inline.
        result.path = None
        return result.to_dict()
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


def fetch_remote(file_id: str, base_path: str, attachment: str | None, *, recursive: bool = False, tabs: list[str] | None = None) -> dict[str, Any]:
    """
    Remote fetch: deposit to temp dir, read content back inline, clean up.

    Fetchers work unchanged — they write to a temp dir instead of the caller's
    cwd. We read the deposited content back and include it in the response.
    """
    if base_path:
        effective_base = Path(base_path)
        temp_dir = None
    else:
        temp_dir = tempfile.mkdtemp(prefix="mise-remote-")
        effective_base = Path(temp_dir)
    try:
        result = do_fetch(file_id, base_path=effective_base, attachment=attachment, recursive=recursive, tabs=tabs)

        if not isinstance(result, FetchResult):
            return result.to_dict()

        # Binary formats (images) can't be inlined as text.
        # Return metadata and cues but no content body.
        if result.format not in ("markdown", "csv", "json", "text"):
            result.cues.setdefault("warnings", []).append(
                f"Binary content ({result.format}) cannot be returned inline in remote mode"
            )
            return result.to_dict()

        # Read content back from the deposited file
        content_path = Path(result.content_file)
        if content_path.exists():
            result.content = content_path.read_text(encoding="utf-8", errors="replace")

        # Read comments if present
        comments_path = content_path.parent / "comments.md"
        if comments_path.exists():
            result.comments = comments_path.read_text(encoding="utf-8", errors="replace")

        return result.to_dict()
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
