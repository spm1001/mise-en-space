#!/usr/bin/env python3
"""
Google Workspace MCP v2 Server

Filesystem-first, token-efficient MCP server for Google Workspace.

Verb model (3 tools):
- search: Unified discovery across Drive/Gmail
- fetch: Content to filesystem, return path
- create: Markdown → Doc/Sheet/Slides

Documentation is provided via MCP Resources, not a tool.

Architecture:
- extractors/: Pure functions (no MCP, no API calls)
- adapters/: Thin Google API wrappers
- tools/: Tool implementations (business logic)
- workspace/: Per-session folder management
- server.py: Thin MCP wrappers (this file)
"""

from mcp.server.fastmcp import FastMCP

from tools import do_search, do_fetch, do_create

# Initialize MCP server
mcp = FastMCP("Google Workspace v2")


# ============================================================================
# TOOLS — Verb Model (thin wrappers)
# ============================================================================

@mcp.tool()
def search(
    query: str,
    sources: list[str] = None,
    max_results: int = 20
) -> dict:
    """
    Search across Drive and Gmail.

    Returns metadata + snippets for triage. No files written.

    Args:
        query: Search terms
        sources: ['drive', 'gmail'] — default: both
        max_results: Maximum results per source

    Returns:
        Separate lists per source (drive_results, gmail_results)
    """
    return do_search(query, sources, max_results).to_dict()


@mcp.tool()
def fetch(file_id: str) -> dict:
    """
    Fetch content to filesystem.

    Writes processed content to mise-fetch/ in current directory.
    Returns path for caller to read with standard file tools.

    Always optimizes for LLM consumption (markdown, CSV, clean text).
    Auto-detects ID type (Drive file vs Gmail thread vs URL).

    Args:
        file_id: Drive file ID, Gmail thread ID, or URL

    Returns:
        path: Filesystem path to fetched content folder
        content_file: Path to main content file
        format: Output format (markdown, csv)
        type: Content type (doc, sheet, slides, gmail)
        metadata: File metadata
    """
    return do_fetch(file_id).to_dict()


@mcp.tool()
def create(
    content: str,
    title: str,
    doc_type: str = 'doc',
    folder_id: str = None
) -> dict:
    """
    Create Google Workspace document from markdown.

    Args:
        content: Markdown content
        title: Document title
        doc_type: 'doc' | 'sheet' | 'slides'
        folder_id: Optional destination folder

    Returns:
        file_id: Created file ID
        web_link: URL to view/edit
    """
    return do_create(content, title, doc_type, folder_id).to_dict()


# ============================================================================
# SERVER ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    mcp.run()
