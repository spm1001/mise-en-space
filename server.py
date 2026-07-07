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
import logging
import os
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from adapters.conversion import cleanup_orphaned_temp_files
from logging_config import configure_call_logging, log_mcp_call
from tools import do_search, do_fetch
from tools.dispatch import DO_DESCRIPTION_FULL, DO_DESCRIPTION_REMOTE, run_operation
from tools.remote import REMOTE_ALLOWED_OPS, fetch_remote, search_remote
from tools.search import VALID_TYPE_FILTERS, CANONICAL_TYPE_NAMES
from resources.docs import register_docs_resources
from resources.tools import get_tool_registry

logger = logging.getLogger(__name__)


# Determined early (before decorators run) so tool descriptions can adapt.
# Uses sys.argv + env var because @mcp.tool() fires at import time, before
# argparse runs in __main__. The argparse block in __main__ validates properly.
_REMOTE_MODE = "--remote" in sys.argv or os.environ.get("MISE_REMOTE") == "1"


@asynccontextmanager
async def lifespan(app: FastMCP) -> AsyncIterator[None]:
    """Run startup tasks — best-effort orphan cleanup."""
    try:
        count = await asyncio.to_thread(cleanup_orphaned_temp_files)
        if count:
            logger.info(f"Startup: cleaned up {count} orphaned temp files")
    except Exception as e:
        logger.debug(f"Startup orphan cleanup skipped: {e}")
    yield

# Initialize MCP server
mcp = FastMCP("Google Workspace v2", lifespan=lifespan)


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
) -> dict[str, Any]:
    """
    Search across Drive and Gmail.

    Writes results to .mise/ and returns path + summary.
    Read the deposited JSON file for full results.

    Args:
        query: Search terms. Optional when type or folder_id is set.
        sources: ['drive', 'gmail'] — default: both (drive only in guest mode). Also: 'activity' (recent comments), 'calendar' (events ±7 days, query-filtered, nearest-now kept when capped)
        max_results: Maximum results per source
        base_path: Directory for deposits (pass your cwd so files land next to your project, not the MCP server's directory)
        folder_id: Optional Drive folder ID to scope results to immediate children only.
            Non-recursive — only files directly inside this folder are returned.
            When set, forces sources=['drive'] (Gmail has no folder concept).
        type: Optional Drive file type filter. Applies to Drive only.
            Values: folder, doc, spreadsheet, sheet, slides, presentation, pdf, image, video, form

    Returns:
        path: Path to deposited search results JSON
        query: The search query
        sources: Sources searched
        drive_count: Number of Drive results
        gmail_count: Number of Gmail results
        activity_count: Number of Activity results
        calendar_count: Number of Calendar results
        cues: Scope notes and warnings
    """
    if not query.strip() and type is None and folder_id is None:
        return {"error": True, "kind": "invalid_input",
                "message": "search requires at least one of: query, type, or folder_id"}

    if type is not None and type not in VALID_TYPE_FILTERS:
        return {"error": True, "kind": "invalid_input",
                "message": f"Unknown type '{type}'. Valid: {', '.join(sorted(CANONICAL_TYPE_NAMES))}"}

    call_params: dict[str, Any] = {"query": query, "sources": sources, "max_results": max_results}
    if folder_id:
        call_params["folder_id"] = folder_id
    if type:
        call_params["type"] = type

    if _REMOTE_MODE:
        result = search_remote(query, sources, max_results, base_path, folder_id, type)
        _log_search_result(call_params, result)
        return result

    if not base_path:
        return {"error": True, "kind": "invalid_input",
                "message": "base_path is required — pass your working directory so deposits land in your project, not the MCP server's directory"}
    result = do_search(query, sources, max_results, base_path=Path(base_path), folder_id=folder_id, type=type).to_dict()
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
def fetch(file_id: str, base_path: str = "", attachment: str | None = None, tabs: list[str] | None = None, recursive: bool = False) -> dict[str, Any]:
    """
    Fetch content to .mise/ — auto-detects type (Drive file, Gmail thread, folder).

    Pass base_path=cwd. Use attachment= for specific Gmail attachments (Office/PDF/image).
    Use recursive=True on folders for full tree. Use tabs= to fetch specific spreadsheet tabs.
    """
    call_params: dict[str, Any] = {"file_id": file_id}
    if attachment:
        call_params["attachment"] = attachment
    if recursive:
        call_params["recursive"] = True
    if tabs:
        call_params["tabs"] = tabs

    if _REMOTE_MODE:
        result = fetch_remote(file_id, base_path, attachment, recursive=recursive, tabs=tabs)
        _log_fetch_result(call_params, result)
        return result

    if not base_path:
        return {"error": True, "kind": "invalid_input",
                "message": "base_path is required — pass your working directory so deposits land in your project, not the MCP server's directory"}
    result = do_fetch(file_id, base_path=Path(base_path), attachment=attachment, recursive=recursive, tabs=tabs).to_dict()
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
    ]:
        if v is not None and v is not False:
            call_params[k] = v
    if content is not None:
        call_params["content_len"] = len(content)

    # In remote mode, reject operations outside the safe subset.
    # Error message lists only allowed ops — don't leak restricted op names.
    if _REMOTE_MODE and operation not in REMOTE_ALLOWED_OPS:
        msg = f"Operation not available in remote mode. Supported: {sorted(REMOTE_ALLOWED_OPS)}"
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
