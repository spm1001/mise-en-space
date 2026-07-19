"""
do() dispatch — the operation table, per-op validation, and execution.

Thirteen-plus operations route through one MCP tool, so the knowledge of
"which params does each op need" lives here (runtime validation) rather
than in the MCP schema — a deliberate token-budget trade-off (the do()
tool description stays compact; see understanding.md "generic primitive").

server.py's do() wrapper handles logging and the remote-mode gate, then
calls run_operation(). Tests verify OPERATIONS/DISPATCH/REQUIRED_PARAMS
stay in sync automatically (tests/unit/test_dispatch.py).
"""

from typing import Any

from adapters.drive import get_file_metadata
from models import MiseError
from tools import (
    OPERATIONS,
    do_append,
    do_archive,
    do_comment,
    do_comment_reply,
    do_create,
    do_draft,
    do_label,
    do_move,
    do_overwrite,
    do_prepend,
    do_rename,
    do_replace_text,
    do_reply_draft,
    do_setup_oauth,
    do_share,
    do_star,
    do_trash,
)

# Required params per operation — validated before dispatch.
# Only lists unconditionally required params (e.g. file_id for move).
# Conditional requirements (create needs content OR source) stay in handlers.
REQUIRED_PARAMS: dict[str, set[str]] = {
    "create": set(),  # content OR source — handler validates
    "move": {"file_id"},  # folder_id OR destination_folder_id (alias) — handler validates
    "rename": {"file_id", "title"},
    "share": {"file_id", "to"},
    "overwrite": {"file_id"},  # content OR source — handler validates
    "prepend": {"file_id", "content"},
    "append": {"file_id", "content"},
    "replace_text": {"file_id", "find", "content"},
    "draft": {"content"},  # create needs to+subject too; update (file_id) doesn't — handler validates
    "reply_draft": {"file_id", "content"},
    "archive": {"file_id"},
    "star": {"file_id"},
    "label": {"file_id", "label"},
    "comment": {"file_id", "content"},  # opens a new (unanchored) thread
    "comment_reply": {"file_id", "comment_id"},  # content OR action — handler validates
    "setup_oauth": set(),  # no required params — force=true is optional
    "trash": {"file_id"},
}

# Content operations that need mime-type routing (metadata pre-fetched at dispatch)
CONTENT_OPS = {"overwrite", "prepend", "append", "replace_text"}

# Dispatch table for do() operations.
# Each handler receives the full params dict and handles its own validation.
DISPATCH: dict[str, Any] = {
    "create": lambda p: do_create(
        content=p["content"], title=p["title"], doc_type=p["doc_type"],
        folder_id=p["folder_id"], source=p["source"], base_path=p["base_path"],
        file_path=p.get("file_path"), page_setup=p.get("page_setup"),
    ),
    "move": lambda p: do_move(
        file_id=p["file_id"], folder_id=p["folder_id"],
        destination_folder_id=p["destination_folder_id"],
    ),
    "rename": lambda p: do_rename(
        file_id=p["file_id"], title=p["title"],
    ),
    "share": lambda p: do_share(
        file_id=p["file_id"], to=p["to"], role=p.get("role"),
        confirm=p.get("confirm", False),
    ),
    "overwrite": lambda p: do_overwrite(
        file_id=p["file_id"], content=p["content"],
        source=p["source"], base_path=p["base_path"],
        metadata=p.get("_metadata"),
        file_path=p.get("file_path"),
    ),
    "prepend": lambda p: do_prepend(file_id=p["file_id"], content=p["content"], metadata=p.get("_metadata")),
    "append": lambda p: do_append(file_id=p["file_id"], content=p["content"], metadata=p.get("_metadata")),
    "replace_text": lambda p: do_replace_text(
        file_id=p["file_id"], find=p["find"], content=p["content"],
        metadata=p.get("_metadata"),
    ),
    "draft": lambda p: do_draft(
        to=p["to"], subject=p["subject"], content=p["content"],
        cc=p["cc"], include=p["include"], file_id=p["file_id"],
    ),
    "reply_draft": lambda p: do_reply_draft(
        file_id=p["file_id"], content=p["content"],
        cc=p["cc"], include=p["include"], reply_all=p.get("reply_all", False),
    ),
    "archive": lambda p: do_archive(file_id=p["file_id"]),
    "star": lambda p: do_star(file_id=p["file_id"]),
    "label": lambda p: do_label(
        file_id=p["file_id"], label=p.get("label"),
        remove=p.get("remove", False),
    ),
    "comment": lambda p: do_comment(
        file_id=p["file_id"], content=p.get("content"),
    ),
    "comment_reply": lambda p: do_comment_reply(
        file_id=p["file_id"], comment_id=p.get("comment_id"),
        content=p.get("content"), action=p.get("action"),
    ),
    "setup_oauth": lambda p: do_setup_oauth(force=p.get("force", False)),
    "trash": lambda p: do_trash(file_id=p["file_id"]),
}


# Tool descriptions — server.py picks one at decoration time based on _REMOTE_MODE.
DO_DESCRIPTION_FULL = """\
Act on Google Workspace — create, move, edit, draft/reply emails, organise Gmail.

Operations: create, move, rename, share, overwrite, prepend, append, replace_text, draft, reply_draft, archive, star, label, comment, comment_reply, trash, setup_oauth.
Create: content + title + doc_type (doc/sheet/slides/file/folder/form). page_setup='pageless' for pageless docs. file_path= to read from disk. folder: title only, no content needed. form: content is YAML/JSON spec with title, description, questions.
Edit: overwrite (full replace), prepend/append (add to), replace_text (find + content). Sheets: overwrite=CSV replaces first tab; replace_text=cell find/replace. Forms: overwrite takes the same spec as create — fetch, tweak, overwrite (replaces all questions).
Email: draft (to + subject + content; file_id=draft_id updates that draft in place), reply_draft (file_id + content), archive/star/label. Drafts auto-append the user's Gmail signature — don't write a sign-off in content.
Trash: file_id (single or list) — Drive files go to recoverable trash; Gmail draft IDs (r+digits) are discarded permanently.
Comments: comment (file_id + content — opens a NEW thread), comment_reply (file_id + comment_id [from comments.md] + content and/or action=resolve|reopen). Both auto-prefix '[agent] '.
Share: file_id + to + role (reader/writer/commenter), confirm=True to execute.
Move: file_id (single or list) + folder_id (alias: destination_folder_id).
setup_oauth: bootstrap Google credentials when none exist. Opens a browser for consent; saves token to Keychain. force=true to re-auth."""

DO_DESCRIPTION_REMOTE = """\
Act on Google Workspace (remote mode — safe operations only).

Args:
    operation: What to do. One of: 'create', 'draft', 'reply_draft', 'archive', 'star', 'label'
    content: Text content (required for create, draft, reply_draft).
        Drafts auto-append the user's Gmail signature — don't write a sign-off.
    title: Document title (for create)
    doc_type: 'doc' | 'sheet' | 'slides' | 'form' (for create). form: content is YAML/JSON spec
    folder_id: Optional destination folder (for create)
    file_id: Target thread ID (for reply_draft, archive, star, label)
    to: Recipient email address(es), comma-separated (for draft)
    subject: Email subject (for draft)
    cc: CC address(es), comma-separated (for draft, reply_draft)
    include: List of Drive file IDs to include as links in the email body (for draft, reply_draft)
    reply_all: If True, infer Cc from all recipients on the last message (for reply_draft)
    label: Label name to add/remove (for label operation; resolved to ID automatically)
    remove: If True, remove the label instead of adding it (for label operation)

Returns:
    file_id: File ID, draft ID, or thread ID
    web_link: URL to view/edit"""


def run_operation(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    """
    Validate and execute one do() operation.

    Returns the operation's result dict on success, or an error dict
    ({"error": True, "kind": ..., "message": ...}) on any failure.
    Never raises — handler exceptions are caught and wrapped.

    Note the kind-casing wart, preserved for compatibility: unknown-op
    returns "invalid_input" (lowercase), missing-params returns
    "INVALID_INPUT" (uppercase). Tests pin both.
    """
    handler = DISPATCH.get(operation)
    if not handler:
        return {"error": True, "kind": "invalid_input",
                "message": f"Unknown operation: {operation}. Supported: {sorted(OPERATIONS)}"}

    required = REQUIRED_PARAMS.get(operation, set())
    missing = {p for p in required if params.get(p) is None}
    if missing:
        return {"error": True, "kind": "INVALID_INPUT",
                "message": f"'{operation}' requires: {', '.join(sorted(missing))}"}

    # Pre-fetch metadata for content operations — one Drive API call shared
    # by routing logic and handler, instead of each handler fetching its own.
    if operation in CONTENT_OPS and params.get("file_id"):
        try:
            params["_metadata"] = get_file_metadata(params["file_id"])
        except MiseError as e:
            return {"error": True, "kind": e.kind.value, "message": e.message}

    try:
        result = handler(params)
    except Exception as e:
        return {"error": True, "kind": "INTERNAL",
                "message": f"Operation '{operation}' failed: {e}", "retryable": False}
    result_dict: dict[str, Any] = result.to_dict() if hasattr(result, "to_dict") else result
    return result_dict
