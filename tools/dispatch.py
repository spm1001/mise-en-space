"""
do() dispatch — the operation table, per-op validation, and execution.

Thirteen-plus operations route through one MCP tool, so the knowledge of
"which params does each op need" lives here (runtime validation) rather
than in the MCP schema — a deliberate token-budget trade-off (the do()
tool description stays compact; see understanding.md "generic primitive").
That trade-off has a cost the schema would otherwise carry: every param
validates for every op, so this module also has to say which params each
op CONSUMES (OP_PARAMS) — anything else is dropped without a word.

server.py's do() wrapper handles logging and the remote-mode gate, then
calls run_operation(). Tests verify OPERATIONS/DISPATCH/REQUIRED_PARAMS
and OP_PARAMS stay in sync automatically (tests/unit/test_dispatch.py).
"""

from typing import Any

from adapters.drive import get_file_metadata
from models import MiseError
from token_store import ambient_mode
from validation import diagnose_sa_quota_403
from tools import (
    OPERATIONS,
    do_append,
    do_archive,
    do_comment,
    do_comment_reply,
    do_copy,
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
    do_suggest,
    do_share,
    do_star,
    do_trash,
    do_respond,
    do_create_event,
    do_update_event,
    do_freebusy,
)

# Required params per operation — validated before dispatch.
# Only lists unconditionally required params (e.g. file_id for move).
# Conditional requirements (create needs content OR source) stay in handlers.
REQUIRED_PARAMS: dict[str, set[str]] = {
    "create": set(),  # content OR source — handler validates
    "copy": {"file_id"},  # folder_id optional — Drive defaults to beside the original
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
    "comment": {"file_id", "content"},  # anchor= optional; without it the thread is panel-only
    "comment_reply": {"file_id", "comment_id"},  # content OR action — handler validates
    "setup_oauth": set(),  # no required params — force=true is optional
    "trash": {"file_id"},
    "respond": {"file_id", "action"},
    "create_event": {"title", "time_min", "time_max"},
    "update_event": {"file_id"},
    "freebusy": {"attendees", "time_min", "time_max"},
    "suggest": {"file_id", "action", "find"},  # fold a tracked change in or out
}

# Content operations that need mime-type routing (metadata pre-fetched at dispatch)
CONTENT_OPS = {"overwrite", "prepend", "append", "replace_text"}

# The whole do() param surface with its defaults — one flat list serving all
# 22 operations, mirroring server.py's do() signature. This is the single
# source of truth for it: mise_en_space imports it as _DO_DEFAULTS to build a
# complete params dict, and run_operation reads it to tell "the caller passed
# this" from "the signature default arrived", which it otherwise cannot —
# server.py's do() sends every key on every call, most of them None.
# tests/unit/test_facade.py::test_do_defaults_mirror_servers_signature pins it
# against the real signature, so a param added there without an entry here
# fails loudly.
DO_PARAM_DEFAULTS: dict[str, Any] = {
    "content": None, "title": None, "doc_type": "doc", "folder_id": None,
    "page_setup": None, "file_id": None, "destination_folder_id": None,
    "source": None, "base_path": None, "file_path": None, "find": None,
    "to": None, "subject": None, "cc": None, "include": None,
    "reply_all": False, "role": None, "confirm": False, "label": None,
    "remove": False, "comment_id": None, "action": None, "force": False,
    "restore_comment": True, "supersede": False, "range": None, "tab": None,
    "anchor": None, "suggest": False,
    "attendees": None, "time_min": None, "time_max": None, "location": None,
    "meet": False, "recurrence": None, "send_updates": None, "duration": None,
    "properties": None, "color": None, "visibility": None, "transparency": None,
}

# Which params each op actually CONSUMES — read straight off the DISPATCH
# lambdas below, in the same order, so the two can be diffed by eye.
#
# Why this exists: do() is one tool with one flat param list, so the MCP schema
# accepts every param name for every operation. A param an op's lambda doesn't
# pass is dropped on the floor — the handler never sees it, nothing warns, and
# the operation succeeds having ignored what the caller asked for. That is this
# codebase's characteristic bug (mise-fumuda). The tab= rail shipped for
# mise-wisuzu was the one-param version of this map.
#
# The map records what the lambda PASSES, not what the handler goes on to use,
# and that direction is deliberate: erring wide can only leave the gate too
# permissive (which is the status quo), never refuse a call that would have
# worked. tests/unit/test_dispatch.py::TestOpParamsMatchDispatch pins it
# against the lambdas by parsing them, so drift fails there rather than in
# production — in BOTH directions: under-claiming here makes the gate refuse a
# param the op really does take (mutation-controlled, 2026-08-24).
OP_PARAMS: dict[str, frozenset[str]] = {
    "create": frozenset({"content", "title", "doc_type", "folder_id", "source",
                         "base_path", "file_path", "page_setup"}),
    "copy": frozenset({"file_id", "folder_id", "title"}),
    "move": frozenset({"file_id", "folder_id", "destination_folder_id"}),
    "rename": frozenset({"file_id", "title"}),
    "share": frozenset({"file_id", "to", "role", "confirm"}),
    "overwrite": frozenset({"file_id", "content", "source", "base_path",
                            "file_path", "restore_comment", "range"}),
    "prepend": frozenset({"file_id", "content", "suggest"}),
    "append": frozenset({"file_id", "content", "tab", "suggest"}),
    "replace_text": frozenset({"file_id", "find", "content", "suggest"}),
    "draft": frozenset({"to", "subject", "content", "cc", "include", "file_id"}),
    "reply_draft": frozenset({"file_id", "content", "cc", "include",
                              "reply_all", "supersede"}),
    "archive": frozenset({"file_id"}),
    "star": frozenset({"file_id"}),
    "label": frozenset({"file_id", "label", "remove"}),
    "comment": frozenset({"file_id", "content", "anchor", "to"}),
    "comment_reply": frozenset({"file_id", "comment_id", "content", "action"}),
    "setup_oauth": frozenset({"force"}),
    "trash": frozenset({"file_id"}),
    "respond": frozenset({"file_id", "action"}),
    "create_event": frozenset({"title", "time_min", "time_max", "content",
                               "attendees", "location", "meet", "recurrence",
                               "include", "send_updates", "properties", "color",
                               "visibility", "transparency", "confirm"}),
    "update_event": frozenset({"file_id", "title", "content", "location",
                               "time_min", "time_max", "attendees", "recurrence",
                               "include", "meet", "send_updates", "properties",
                               "color", "visibility", "transparency", "confirm"}),
    "freebusy": frozenset({"attendees", "time_min", "time_max", "duration"}),
    "suggest": frozenset({"file_id", "action", "find"}),
}

# param -> the ops that consume it. Inverted rather than written out twice.
PARAM_OWNERS: dict[str, frozenset[str]] = {
    param: frozenset(op for op, consumed in OP_PARAMS.items() if param in consumed)
    for param in DO_PARAM_DEFAULTS
}

# Params exempt from the wrong-op gate — policy, deliberately kept apart from
# the consumption record above so neither can be edited by accident.
#
# base_path is the deposit root: create/overwrite are the only ops that read a
# deposit, but mise_en_space's Mise(base_path=...) stamps it onto EVERY do()
# call it makes. Gating it would refuse every facade call from a base_path-
# configured handle — a live break in the library door, not a hypothetical.
UNGATED_PARAMS = frozenset({"base_path"})

# Extra teaching for params whose owner list alone doesn't explain the miss.
# The tab= text is the wisuzu rail's, kept verbatim.
PARAM_HINTS: dict[str, str] = {
    "tab": "do(append, file_id=…, content=…, tab='Title') places content in a "
           "NEW tab of an existing Google Doc. Writing INTO an existing tab "
           "isn't supported — prepend/append address the first tab, "
           "replace_text applies across ALL tabs, and rich overwrite cannot "
           "target a tab.",
    "file_path": "file_path= reads a file from the SERVER's disk as the whole "
                 "new body; the surgical edits take the text itself as content=.",
    "source": "source= replays a previously fetched .mise deposit as the whole "
              "new body; the surgical edits take the text itself as content=.",
    "range": "range= is Sheets A1 notation ('Tab' or 'Tab!F9:F15') scoping how "
             "much of a sheet a full replace clears — a blast-radius limiter, "
             "not an insertion point.",
    "file_id": "file_id= names an artefact that already exists; to change one, "
               "reach for overwrite/prepend/append/replace_text.",
}


# Dispatch table for do() operations.
# Each handler receives the full params dict and handles its own validation.
DISPATCH: dict[str, Any] = {
    "create": lambda p: do_create(
        content=p["content"], title=p["title"], doc_type=p["doc_type"],
        folder_id=p["folder_id"], source=p["source"], base_path=p["base_path"],
        file_path=p.get("file_path"), page_setup=p.get("page_setup"),
    ),
    "copy": lambda p: do_copy(
        file_id=p["file_id"], folder_id=p["folder_id"], title=p["title"],
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
        restore_comment=p.get("restore_comment", True),
        range_=p.get("range"),
    ),
    "prepend": lambda p: do_prepend(file_id=p["file_id"], content=p["content"],
                                    metadata=p.get("_metadata"), suggest=p.get("suggest", False)),
    "append": lambda p: do_append(
        file_id=p["file_id"], content=p["content"],
        metadata=p.get("_metadata"), tab=p.get("tab"), suggest=p.get("suggest", False),
    ),
    "replace_text": lambda p: do_replace_text(
        file_id=p["file_id"], find=p["find"], content=p["content"],
        metadata=p.get("_metadata"), suggest=p.get("suggest", False),
    ),
    "draft": lambda p: do_draft(
        to=p["to"], subject=p["subject"], content=p["content"],
        cc=p["cc"], include=p["include"], file_id=p["file_id"],
    ),
    "reply_draft": lambda p: do_reply_draft(
        file_id=p["file_id"], content=p["content"],
        cc=p["cc"], include=p["include"], reply_all=p.get("reply_all", False),
        supersede=p.get("supersede", False),
    ),
    "archive": lambda p: do_archive(file_id=p["file_id"]),
    "star": lambda p: do_star(file_id=p["file_id"]),
    "label": lambda p: do_label(
        file_id=p["file_id"], label=p.get("label"),
        remove=p.get("remove", False),
    ),
    "comment": lambda p: do_comment(
        file_id=p["file_id"], content=p.get("content"),
        anchor=p.get("anchor"), to=p.get("to"),
    ),
    "comment_reply": lambda p: do_comment_reply(
        file_id=p["file_id"], comment_id=p.get("comment_id"),
        content=p.get("content"), action=p.get("action"),
    ),
    "suggest": lambda p: do_suggest(
        file_id=p["file_id"], action=p.get("action"), find=p.get("find"),
    ),
    "setup_oauth": lambda p: do_setup_oauth(force=p.get("force", False)),
    "trash": lambda p: do_trash(file_id=p["file_id"]),
    "respond": lambda p: do_respond(file_id=p["file_id"], action=p["action"]),
    "create_event": lambda p: do_create_event(
        title=p["title"], time_min=p.get("time_min"), time_max=p.get("time_max"),
        content=p["content"], attendees=p.get("attendees"),
        location=p.get("location"), meet=p.get("meet", False),
        recurrence=p.get("recurrence"), include=p["include"],
        send_updates=p.get("send_updates"), properties=p.get("properties"),
        color=p.get("color"), visibility=p.get("visibility"),
        transparency=p.get("transparency"), confirm=p.get("confirm", False),
    ),
    "update_event": lambda p: do_update_event(
        file_id=p["file_id"], title=p["title"], content=p["content"],
        location=p.get("location"), time_min=p.get("time_min"),
        time_max=p.get("time_max"), attendees=p.get("attendees"),
        recurrence=p.get("recurrence"), include=p["include"],
        meet=p.get("meet", False), send_updates=p.get("send_updates"),
        properties=p.get("properties"), color=p.get("color"),
        visibility=p.get("visibility"), transparency=p.get("transparency"),
        confirm=p.get("confirm", False),
    ),
    "freebusy": lambda p: do_freebusy(
        attendees=p.get("attendees"), time_min=p.get("time_min"),
        time_max=p.get("time_max"), duration=p.get("duration"),
    ),
}


# Tool descriptions — server.py picks one at decoration time based on _REMOTE_MODE.
DO_DESCRIPTION_FULL = """\
Act on Google Workspace: create, move, edit, draft/reply email, organise Gmail, book calendar.

Operations: create, copy, move, rename, share, overwrite, prepend, append, replace_text, draft, reply_draft, archive, star, label, comment, comment_reply, suggest, trash, respond, create_event, update_event, freebusy, setup_oauth.
Create: content + title + doc_type (doc/sheet/file/folder/form). page_setup='pageless'; file_path= reads disk; form: YAML/JSON.
Edit: overwrite (full replace), prepend/append, replace_text (find + content); append tab='T' adds a new doc tab (plain text). Sheets: overwrite=CSV, range='Tab'/'Tab!F9:F15'; [label](url)→link, @url→chip. Doc edits return cues.restore_point. Docs: suggest=True on prepend/append/replace_text proposes a tracked change; do(suggest, action=accept|reject, find='s2') folds one ([sN] from a markup fetch; tags RENUMBER after each fold).
Calendar: create_event (title + time_min/time_max + attendees/content/location/meet/recurrence/include; attendees ⇒ preview, confirm=True books). update_event (event or invite-thread id; time/attendees/recurrence gated). freebusy (attendees + window + duration → slots). respond (file_id + action). properties/color/visibility/transparency on both. See mise://docs/do.
Email: draft (to + subject + content; file_id=draft_id updates it), reply_draft (file_id + content — refuses if the thread has a draft; supersede=True discards), archive/star/label. Signature auto-appends — no sign-off.
Trash: file_id(s) — Drive→trash (recoverable); Gmail drafts (r+digits) go for good.
Comments: comment (file_id + content = NEW thread; anchor='slide 3'/'Tab!B12'/quoted doc text anchors it, else panel-only; to= assigns), comment_reply (comment_id + content/action=resolve|reopen). '[agent] ' prefix.
Share: file_id + to + role (reader/writer/commenter); confirm=True executes.
Copy/Move: file_id(s) + folder_id.
setup_oauth: bootstrap credentials (force=true re-auths)."""

DO_DESCRIPTION_REMOTE = """\
Act on Google Workspace (remote mode — safe operations only).

Args:
    operation: What to do. One of: 'create', 'draft', 'reply_draft', 'archive', 'star', 'label'
    content: Text content (required for create, draft, reply_draft).
        Drafts auto-append the user's Gmail signature — don't write a sign-off.
    title: Document title (for create)
    doc_type: 'doc' | 'sheet' | 'file' | 'folder' | 'form' (for create). form: content is YAML/JSON spec
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


# Ops that need a Gmail mailbox, a personal calendar, or an OAuth flow —
# a service account has none of these, so ambient mode (mise-wasagu)
# refuses up front with the reason rather than leaving Google to answer
# an opaque insufficient-scope 403. trash stays available: it is dual-
# backend and its Drive half works (a Gmail-id trash errors per-call).
AMBIENT_UNAVAILABLE_OPS = {
    "draft", "reply_draft", "archive", "star", "label", "respond", "setup_oauth",
    "create_event", "update_event", "freebusy",
}


def wrong_op_params(operation: str, params: dict[str, Any]) -> list[str]:
    """Params the caller supplied that `operation` will silently ignore.

    "Supplied" means present AND different from do()'s signature default —
    server.py sends every key on every call, so presence alone proves nothing.
    Sorted, so the message is stable.
    """
    return sorted(
        param
        for param, owners in PARAM_OWNERS.items()
        if operation not in owners
        and param not in UNGATED_PARAMS
        and param in params
        and params[param] != DO_PARAM_DEFAULTS[param]
    )


def _wrong_op_message(operation: str, offenders: list[str]) -> str:
    """Teaching text: what was ignored, and which op takes it instead."""
    named = ", ".join(f"{p}=" for p in offenders)
    parts = [
        (
            f"'{operation}' does not take {named} — do()'s param list is flat "
            f"across all {len(DISPATCH)} operations, so what an operation doesn't "
            "consume is dropped in silence rather than refused by the schema."
        )
    ]
    for param in offenders:
        owners = PARAM_OWNERS[param]
        parts.append(
            f"{param}= applies to: {', '.join(sorted(owners))}."
            if owners else
            f"{param}= is consumed by no operation."
        )
        hint = PARAM_HINTS.get(param)
        if hint:
            parts.append(hint)
    return " ".join(parts)


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

    if operation in AMBIENT_UNAVAILABLE_OPS and ambient_mode():
        return {"error": True, "kind": "invalid_input",
                "message": f"'{operation}' is unavailable in ambient (service-account) "
                           "mode — a service account has no Gmail mailbox or personal "
                           "calendar, and its credentials come from the platform, not "
                           "an OAuth flow. Drive-family ops (create, copy, move, "
                           "rename, share, overwrite, prepend, append, replace_text, "
                           "comment, comment_reply, trash) remain available."}

    # A param this op doesn't consume would be accepted and silently dropped
    # (OP_PARAMS above). Refuse and name the op that takes it.
    #
    # This runs BEFORE the required-params check on purpose. The two errors
    # compete, and the missing-param one is the misleading half: a caller
    # mirroring overwrite's grammar with do(append, file_id=…, file_path='x.md')
    # was told only "append requires: content" — true, unhelpful, and silent on
    # the param that was the actual mistake (mise-fumuda's motivating case).
    offenders = wrong_op_params(operation, params)
    if offenders:
        return {"error": True, "kind": "invalid_input",
                "message": _wrong_op_message(operation, offenders)}

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
        # The SA-quota 403 carries its cause only in the response BODY,
        # which str(e) drops — surface it with the ownership teaching
        # before the generic INTERNAL swallows it (mise-finupa).
        quota_teaching = diagnose_sa_quota_403(e)
        if quota_teaching:
            return {"error": True, "kind": "permission_denied",
                    "message": quota_teaching, "retryable": False}
        return {"error": True, "kind": "INTERNAL",
                "message": f"Operation '{operation}' failed: {e}", "retryable": False}
    result_dict: dict[str, Any] = result.to_dict() if hasattr(result, "to_dict") else result
    return result_dict
