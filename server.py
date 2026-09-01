#!/usr/bin/env python3
"""
Google Workspace MCP v2 Server

Filesystem-first, token-efficient MCP server for Google Workspace.

Verb model (3 tools):
- search: Unified discovery across Drive/Gmail
- fetch: Content to filesystem (with open comments included automatically)
- do: Act on Workspace (create, move, rename, etc.)

Sous-chef philosophy: when chef asks for a doc, bring the doc AND the comments
AND the context — don't wait to be asked.

Documentation is provided via MCP Resources, not a tool.

Architecture:
- extractors/: Pure functions (no MCP, no API calls)
- adapters/: Thin Google API wrappers
- tools/: Tool implementations (business logic)
- workspace/: Per-session folder management
- server.py: Thin MCP wrappers (this file)
"""

import argparse
import asyncio
import importlib.metadata
import json
import logging
import os
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from adapters.conversion import cleanup_orphaned_temp_files
from logging_config import configure_call_logging, log_mcp_call
from tools import do_search, do_fetch
from tools.dispatch import DO_DESCRIPTION_FULL, DO_DESCRIPTION_REMOTE, run_operation
from tools.remote import REMOTE_ALLOWED_OPS, fetch_remote, search_remote
from tools.search import VALID_TYPE_FILTERS, CANONICAL_TYPE_NAMES
from validation import looks_like_drive_query
from resources.docs import register_docs_resources
from resources.tools import get_tool_registry

logger = logging.getLogger(__name__)


# Determined early (before decorators run) so tool descriptions can adapt.
# Uses sys.argv + env var because @mcp.tool() fires at import time, before
# argparse runs in __main__. The argparse block in __main__ validates properly.
_REMOTE_MODE = "--remote" in sys.argv or os.environ.get("MISE_REMOTE") == "1"

# mcp 2.x runs sync (def) tool handlers on anyio worker threads, so tool
# bodies run CONCURRENTLY — deliberately, since the thread-safety audit
# (mise-bapije, 2026-08-24). The interim `_serialized` threading.Lock from the
# 1.71.0 migration is deleted; the guards that replaced it live where the
# shared state lives: a per-resource lock on the fetch dispatch
# (tools/fetch/router.py), O_EXCL search-deposit naming (workspace/manager.py),
# and a single-flight token-refresh lock (adapters/http_client.py). NB an
# anyio limiter cap is NOT a substitute for any of this: the stdio transport's
# blocked stdin read shares that pool and a capacity of 1 starves tool bodies
# into deadlock (measured 2026-08-23).
@asynccontextmanager
async def lifespan(app: MCPServer) -> AsyncIterator[None]:
    """Run startup tasks — best-effort orphan cleanup."""
    try:
        count = await asyncio.to_thread(cleanup_orphaned_temp_files)
        if count:
            logger.info(f"Startup: cleaned up {count} orphaned temp files")
    except Exception as e:
        logger.debug(f"Startup orphan cleanup skipped: {e}")
    yield

def _plugin_version() -> str:
    """Suite version for serverInfo: plugin.json (stamped at each publish) or
    the installed wheel's dist version; '' when neither resolves — honest,
    never hardcoded (mise-vubeku)."""
    try:
        pj = Path(__file__).resolve().parent / ".claude-plugin" / "plugin.json"
        return str(json.loads(pj.read_text()).get("version") or "")
    except Exception:
        try:
            return importlib.metadata.version("mise-en-space")
        except Exception:
            return ""


# Initialize MCP server
mcp = MCPServer("Google Workspace v2", version=_plugin_version(), lifespan=lifespan)


# ============================================================================
# HEALTH — Kube liveness/readiness probe (no auth required)
# ============================================================================

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# ============================================================================
# TOOLS — Verb Model (thin wrappers)
# ============================================================================

@mcp.tool()
def search(
    query: str = "",
    sources: list[str] | None = None,
    max_results: int = 20,
    base_path: str = "",
    folder_id: str | None = None,
    type: str | None = None,
    raw_query: str | None = None,
    time_min: str | None = None,
    time_max: str | None = None,
    calendar_id: str | None = None,
) -> dict[str, Any]:
    """
    Search across Drive and Gmail.

    Writes results to .mise/ and returns path + summary.
    Read the deposited JSON file for full results.

    Args:
        query: Search terms. Optional when type or folder_id is set.
        sources: ['drive', 'gmail'] — default: both (drive only in guest mode). Also: 'activity' (recent comments), 'calendar' (events, ±7 days unless time_min/time_max), 'people' (staff directory: role, dept, reporting line — mise://docs/search)
        max_results: Maximum results per source
        base_path: Directory for deposits (pass your cwd so files land next to your project, not the MCP server's directory)
        folder_id: Optional Drive folder ID to scope results to immediate children
            only. Non-recursive; forces sources=['drive'] (Gmail has no folders).
        type: Optional Drive file type filter. Applies to Drive only.
            Values: folder, doc, spreadsheet, sheet, slides, presentation, pdf, image, video, form
        raw_query: Drive query language, unescaped (Drive only; excludes other sources).
            Use instead of query when you need what one fullText clause can't say:
            `name contains`, `or`, `not`, `modifiedTime >`, `'x' in owners`.
            `trashed = false` and any type/folder_id are ANDed on.
            NB plain `query` is AND across words — one term the estate doesn't use returns zero.
        time_min: Calendar window start — ISO date/datetime, any range (historical fine),
            no query term needed ('what is in the diary 3–5 Aug?').
        time_max: Window end; a bare date runs to the END of that day.
        calendar_id: colleague's email — their visible diary, ACL-gated; forces sources=['calendar'].

    Returns:
        path: Path to deposited search results JSON; query/sources echoed
        drive_count/gmail_count/activity_count/calendar_count: per-source counts
        cues: Scope notes and warnings
    """
    if query.strip() and raw_query and raw_query.strip():
        return {"error": True, "kind": "invalid_input",
                "message": "Pass either 'query' (search terms) or 'raw_query' (Drive query "
                           "language), not both — they build the same clause two different ways."}

    has_window = bool((time_min or "").strip() or (time_max or "").strip())
    if (not query.strip() and not (raw_query or "").strip() and type is None
            and folder_id is None and not has_window and sources != ["calendar"]):
        return {"error": True, "kind": "invalid_input",
                "message": "search requires at least one of: query, raw_query, type, "
                           "folder_id, or a calendar window (time_min/time_max — or "
                           "sources=['calendar'] alone for the ±7-day listing)"}

    # Drive syntax in `query` doesn't error, it silently keyword-searches the
    # operator names and returns plausible wrong files (probed 2026-07-27:
    # `name contains 'PCA'` returned a 1:1 doc and a probation review). Refusing
    # is strictly better than answering wrongly, and the remedy is one param away.
    if query.strip() and looks_like_drive_query(query):
        return {"error": True, "kind": "invalid_input",
                "message": "That looks like Drive query language, and `query` would search for "
                           "the operator words themselves rather than running it — pass it as "
                           "`raw_query=` instead. Use `query=` for plain search terms."}

    if type is not None and type not in VALID_TYPE_FILTERS:
        return {"error": True, "kind": "invalid_input",
                "message": f"Unknown type '{type}'. Valid: {', '.join(sorted(CANONICAL_TYPE_NAMES))}"}

    call_params: dict[str, Any] = {"query": query, "sources": sources, "max_results": max_results}
    if raw_query:
        call_params["raw_query"] = raw_query
    if folder_id:
        call_params["folder_id"] = folder_id
    if type:
        call_params["type"] = type
    if time_min:
        call_params["time_min"] = time_min
    if time_max:
        call_params["time_max"] = time_max

    # ValueError from do_search is a boundary refusal with teaching text
    # (window vs sources/folder_id, garbage ISO bounds) — same conversion the
    # fetch router applies, so it reaches the caller as JSON, not a traceback.
    if _REMOTE_MODE:
        try:
            result = search_remote(query, sources, max_results, base_path, folder_id, type,
                                   raw_query, time_min=time_min, time_max=time_max)
        except ValueError as e:
            result = {"error": True, "kind": "invalid_input", "message": str(e)}
        _log_search_result(call_params, result)
        return result

    if not base_path:
        return {"error": True, "kind": "invalid_input",
                "message": "base_path is required — pass your working directory so deposits land in your project, not the MCP server's directory"}
    try:
        result = do_search(query, sources, max_results, base_path=Path(base_path),
                           folder_id=folder_id, type=type, raw_query=raw_query,
                           time_min=time_min, time_max=time_max, calendar_id=calendar_id).to_dict()
    except ValueError as e:
        result = {"error": True, "kind": "invalid_input", "message": str(e)}
    _log_search_result(call_params, result)
    return result


def _log_search_result(call_params: dict[str, Any], result: dict[str, Any]) -> None:
    if result.get("error"):
        log_mcp_call("search", params=call_params, ok=False, error=result.get("message"))
    else:
        log_mcp_call("search", params=call_params, result_summary={
            k: result[k] for k in ("drive_count", "gmail_count", "activity_count", "calendar_count")
            if k in result and result[k]
        })


@mcp.tool()
def fetch(file_id: str, base_path: str = "", attachment: str | None = None, tabs: list[str] | None = None, recursive: bool = False, suggestions: str = "accepted", raw: bool = False, thumbnails: bool = True, crops: bool = True) -> dict[str, Any]:
    """
    Fetch content to .mise/ — auto-detects type (Drive file, Gmail thread, folder).

    Pass WHOLE URLs, not extracted ids: ?gid/?tab/#heading/#slide/?disco resolve to a
    cues.pointer naming the deposited artefact (per-tab CSV, content.md line, slide,
    comment); a dangling pointer is reported stale, and a bare id can say none of this.
    Pass base_path=cwd. Use attachment= for specific Gmail attachments (Office/PDF/image).
    recursive=True on folders for full tree; tabs= for specific spreadsheet tabs.
    Docs with suggested edits: suggestions='accepted' (default, applied) | 'original' | 'markup'.
    raw=True with attachment= also deposits the untouched original bytes — PDFs and Office
    files are otherwise converted and the original discarded, so the document itself was
    unreachable. Pairs with do(create, doc_type='file', file_path=...) to put a Gmail-only
    attachment into Drive. thumbnails=False skips page/slide thumbnail rendering;
    crops=False skips PDF embedded-graphic crop extraction (+ its content.md
    anchors) — the two levers for fast text-only corpus walks.
    """
    call_params: dict[str, Any] = {"file_id": file_id}
    if attachment:
        call_params["attachment"] = attachment
    if recursive:
        call_params["recursive"] = True
    if tabs:
        call_params["tabs"] = tabs
    if suggestions != "accepted":
        call_params["suggestions"] = suggestions
    if raw:
        call_params["raw"] = True
    call_params.update({k: False for k, v in (("thumbnails", thumbnails), ("crops", crops)) if not v})

    if raw and not attachment:
        return {"error": True, "kind": "invalid_input",
                "message": "raw=True only applies with attachment= — it deposits that "
                           "attachment's original bytes alongside its extraction."}

    if _REMOTE_MODE:
        # Remote returns content inline in JSON; raw bytes can't be text-encoded,
        # the same reason image fetches carry metadata only in remote mode.
        if raw:
            return {"error": True, "kind": "invalid_input",
                    "message": "raw=True is not available in remote mode — binary content "
                               "cannot be returned inline."}
        result = fetch_remote(file_id, base_path, attachment, recursive=recursive, tabs=tabs, suggestions=suggestions, thumbnails=thumbnails, crops=crops)
        _log_fetch_result(call_params, result)
        return result

    if not base_path:
        return {"error": True, "kind": "invalid_input",
                "message": "base_path is required — pass your working directory so deposits land in your project, not the MCP server's directory"}
    result = do_fetch(file_id, base_path=Path(base_path), attachment=attachment, recursive=recursive, tabs=tabs, suggestions=suggestions, raw=raw, thumbnails=thumbnails, crops=crops).to_dict()
    _log_fetch_result(call_params, result)
    return result


def _log_fetch_result(call_params: dict[str, Any], result: dict[str, Any]) -> None:
    if result.get("error"):
        log_mcp_call("fetch", params=call_params, ok=False, error=result.get("message"))
    else:
        summary: dict[str, Any] = {}
        for k in ("type", "format", "metadata"):
            if k in result:
                val = result[k]
                if k == "metadata" and isinstance(val, dict):
                    summary["title"] = val.get("title")
                else:
                    summary[k] = val
        log_mcp_call("fetch", params=call_params, result_summary=summary)


@mcp.tool(description=DO_DESCRIPTION_REMOTE if _REMOTE_MODE else DO_DESCRIPTION_FULL)
def do(
    operation: str,
    content: str | None = None,
    title: str | None = None,
    doc_type: str = "doc",
    folder_id: str | None = None,
    page_setup: str | None = None,
    file_id: str | list[str] | None = None,
    destination_folder_id: str | None = None,
    source: str | None = None,
    base_path: str | None = None,
    file_path: str | None = None,
    find: str | None = None,
    to: str | None = None,
    subject: str | None = None,
    cc: str | None = None,
    include: list[str] | None = None,
    reply_all: bool = False,
    role: str | None = None,
    confirm: bool = False,
    label: str | None = None,
    remove: bool = False,
    comment_id: str | None = None,
    action: str | None = None,
    force: bool = False,
    restore_comment: bool = True,
    supersede: bool = False,
    range: str | None = None,  # noqa: A002 — MCP property name; A1 notation for Sheets
    tab: str | None = None,
    anchor: str | None = None, suggest: bool = False,  # two on one line: server.py sits at its 500-line cap
    attendees: list[str] | str | None = None,
    time_min: str | None = None,
    time_max: str | None = None,
    location: str | None = None,
    meet: bool = False,
    recurrence: str | list[str] | None = None,
    send_updates: str | None = None,
    duration: int | None = None,
    properties: dict[str, str] | None = None,
    color: str | None = None,
    visibility: str | None = None,
    transparency: str | None = None,
) -> dict[str, Any]:
    """Act on Google Workspace."""
    # Build log params — include operation and non-None values that matter,
    # but skip content (can be huge) and base_path (noise).
    call_params: dict[str, Any] = {"operation": operation}
    for k, v in [
        ("title", title), ("doc_type", doc_type), ("folder_id", folder_id),
        ("file_id", file_id), ("destination_folder_id", destination_folder_id),
        ("source", source), ("file_path", file_path), ("page_setup", page_setup), ("find", find), ("to", to), ("subject", subject),
        ("cc", cc), ("label", label), ("role", role), ("remove", remove),
        ("reply_all", reply_all), ("confirm", confirm),
        ("comment_id", comment_id), ("action", action), ("force", force),
        ("supersede", supersede), ("range", range), ("tab", tab), ("anchor", anchor), ("suggest", suggest),
        ("attendees", attendees), ("time_min", time_min), ("time_max", time_max),
        ("location", location), ("meet", meet), ("recurrence", recurrence),
        ("send_updates", send_updates), ("duration", duration),
        ("properties", properties), ("color", color),
        ("visibility", visibility), ("transparency", transparency),
    ]:
        if v is not None and v is not False:
            call_params[k] = v
    if restore_comment is False:
        call_params["restore_comment"] = False
    if content is not None:
        call_params["content_len"] = len(content)

    # In remote mode, reject operations outside the safe subset.
    # Error message lists only allowed ops — don't leak restricted op names.
    if _REMOTE_MODE and operation not in REMOTE_ALLOWED_OPS:
        msg = f"Operation not available in remote mode. Supported: {sorted(REMOTE_ALLOWED_OPS)}"
        log_mcp_call("do", params=call_params, ok=False, error=msg)
        return {"error": True, "kind": "invalid_input", "message": msg}

    # file_path reads the SERVER's filesystem — meaningful only when the server
    # runs beside the caller (stdio). In remote mode it's a boundary violation:
    # a remote client must never read the host's disk. This is the system
    # boundary; stdio deliberately allows any readable local path (mise-jebude).
    if _REMOTE_MODE and file_path:
        msg = "file_path is not available in remote mode — pass content directly."
        log_mcp_call("do", params=call_params, ok=False, error=msg)
        return {"error": True, "kind": "invalid_input", "message": msg}

    # Remote 'draft' is create-only: update mode (file_id) rewrites an existing
    # draft — destructive to a human's hand-edits, outside the audited safe set.
    if _REMOTE_MODE and operation == "draft" and file_id:
        msg = "Draft update is not available in remote mode — create a new draft instead."
        log_mcp_call("do", params=call_params, ok=False, error=msg)
        return {"error": True, "kind": "invalid_input", "message": msg}

    # Remote supersede would drafts.delete (permanent) — same audited-safe-set
    # boundary as draft update. The guard's refusal still fires remotely.
    if _REMOTE_MODE and supersede:
        msg = "supersede is not available in remote mode (drafts.delete is permanent) — ask the user to discard or send the existing draft in Gmail first."
        log_mcp_call("do", params=call_params, ok=False, error=msg)
        return {"error": True, "kind": "invalid_input", "message": msg}

    params = {
        "content": content, "title": title, "doc_type": doc_type,
        "folder_id": folder_id, "file_id": file_id,
        "destination_folder_id": destination_folder_id,
        "source": source, "base_path": base_path, "file_path": file_path,
        "find": find,
        "to": to, "subject": subject, "cc": cc, "include": include,
        "reply_all": reply_all, "role": role, "confirm": confirm,
        "label": label, "remove": remove,
        "comment_id": comment_id, "action": action,
        "page_setup": page_setup, "force": force,
        "restore_comment": restore_comment, "supersede": supersede,
        "range": range, "tab": tab, "anchor": anchor, "suggest": suggest,
        "attendees": attendees, "time_min": time_min, "time_max": time_max,
        "location": location, "meet": meet, "recurrence": recurrence,
        "send_updates": send_updates, "duration": duration,
        "properties": properties, "color": color,
        "visibility": visibility, "transparency": transparency,
    }

    # Validation, metadata prefetch, and execution live in tools/dispatch.py.
    result_dict = run_operation(operation, params)

    if isinstance(result_dict, dict) and result_dict.get("error"):
        log_mcp_call("do", params=call_params, ok=False, error=result_dict.get("message"))
        return result_dict

    summary: dict[str, Any] = {}
    if isinstance(result_dict, dict):
        for k in ("file_id", "title", "web_link", "operation"):
            if k in result_dict:
                summary[k] = result_dict[k]
    log_mcp_call("do", params=call_params, result_summary=summary)
    return result_dict


# ============================================================================
# RESOURCES — Self-documenting MCP capabilities
# ============================================================================

# Static documentation resources (mise://docs/*, mise://gmail/labels) live in
# resources/docs.py — ~760 lines of text that used to swamp this file.
register_docs_resources(mcp)


# ============================================================================
# AUTO-GENERATED TOOL DOCUMENTATION RESOURCES
# ============================================================================

# Register tool functions for mise://tools/* resource generation
# Must be done after all @mcp.tool() decorators have run
_tool_registry = get_tool_registry()
_tool_registry.register_from_mcp(mcp)


@mcp.resource("mise://tools/{tool_name}")
def tool_resource(tool_name: str) -> str:
    """Auto-generated documentation for a specific tool from its docstring."""
    try:
        resource = _tool_registry.get_resource(f"mise://tools/{tool_name}")
        return resource["text"]
    except KeyError:
        return f"# {tool_name}()\n\nTool not found."


# ============================================================================
# SERVER ENTRY POINT
# ============================================================================

def _shutdown_handler(signum: int, frame: object) -> None:
    """Handle termination signals by exiting immediately.

    os._exit() is required because sys.exit() raises SystemExit,
    which asyncio's event loop catches and ignores. The server
    would survive SIGTERM until stdin closes, causing CC to report
    "1 MCP server failed" on exit.
    """
    os._exit(0)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="mise-en-space MCP server for Google Workspace",
    )
    parser.add_argument(
        "--remote", action="store_true",
        help="Run in remote mode: StreamableHTTP transport, safe operations only. "
             "Also settable via MISE_REMOTE=1 env var.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)
    configure_call_logging()
    if _REMOTE_MODE:
        logger.info("Starting in remote mode (StreamableHTTP on /mcp)")
        logger.info(f"Allowed do() operations: {sorted(REMOTE_ALLOWED_OPS)}")
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
