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

import signal
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from tools import do_search, do_fetch, do_create, do_move
from resources.tools import get_tool_registry

# Initialize MCP server
mcp = FastMCP("Google Workspace v2")


# ============================================================================
# TOOLS — Verb Model (thin wrappers)
# ============================================================================

@mcp.tool()
def search(
    query: str,
    sources: list[str] | None = None,
    max_results: int = 20,
    base_path: str | None = None,
) -> dict[str, Any]:
    """
    Search across Drive and Gmail.

    Writes results to mise/ and returns path + summary.
    Read the deposited JSON file for full results.

    Args:
        query: Search terms
        sources: ['drive', 'gmail'] — default: both
        max_results: Maximum results per source
        base_path: Directory for deposits (pass your cwd so files land next to your project, not the MCP server's directory)

    Returns:
        path: Path to deposited search results JSON
        query: The search query
        sources: Sources searched
        drive_count: Number of Drive results
        gmail_count: Number of Gmail results
    """
    resolved_path = Path(base_path) if base_path else None
    return do_search(query, sources, max_results, base_path=resolved_path).to_dict()


@mcp.tool()
def fetch(file_id: str, base_path: str | None = None, attachment: str | None = None) -> dict[str, Any]:
    """
    Fetch content to filesystem.

    Writes processed content to mise/ in the specified directory.
    Returns path for caller to read with standard file tools.

    Always optimizes for LLM consumption (markdown, CSV, clean text).
    Auto-detects input type and routes appropriately.

    Args:
        file_id: Web URL, Drive file ID, or Gmail thread ID
        base_path: Directory for deposits (pass your cwd so files land next to your project, not the MCP server's directory)
        attachment: Specific attachment filename to extract from a Gmail thread.
                    Use this to extract Office files (DOCX/XLSX/PPTX) that are
                    skipped during normal thread fetch. Also works for PDFs and images.

    Fetch web content with cleaner extraction than curl or WebFetch:
        fetch("https://simonwillison.net/...")  → clean markdown, no boilerplate

    Returns:
        path: Filesystem path to fetched content folder
        content_file: Path to main content file
        format: Output format (markdown, csv)
        type: Content type (doc, sheet, slides, gmail)
        metadata: File metadata
    """
    resolved_path = Path(base_path) if base_path else None
    return do_fetch(file_id, base_path=resolved_path, attachment=attachment).to_dict()


@mcp.tool()
def do(
    operation: str = "create",
    content: str | None = None,
    title: str | None = None,
    doc_type: str = "doc",
    folder_id: str | None = None,
    file_id: str | None = None,
    destination_folder_id: str | None = None,
    source: str | None = None,
    base_path: str | None = None,
) -> dict[str, Any]:
    """
    Act on Google Workspace — create, move, rename, edit.

    Args:
        operation: What to do. One of: 'create', 'move'
        content: Markdown content (required for create, unless source is provided)
        title: Document title (required for create, falls back to manifest title when using source)
        doc_type: 'doc' | 'sheet' | 'slides' (for create)
        folder_id: Optional destination folder (for create)
        file_id: Target file (required for move)
        destination_folder_id: Where to move the file (required for move)
        source: Path to deposit folder containing content to publish (for create).
                Reads content.md (doc) or content.csv (sheet) from the folder.
                Manifest is enriched with creation receipt after success.
        base_path: Directory for resolving relative source paths (pass your cwd)

    Returns:
        file_id: File ID
        web_link: URL to view/edit
    """
    if operation == "create":
        # Resolve source path
        resolved_source = None
        if source:
            resolved_base = Path(base_path) if base_path else Path.cwd()
            source_path = Path(source)
            resolved_source = source_path if source_path.is_absolute() else resolved_base / source_path

        if not content and not source:
            return {"error": True, "kind": "invalid_input",
                    "message": "create requires 'content' or 'source'"}
        return do_create(content, title, doc_type, folder_id, source=resolved_source).to_dict()

    if operation == "move":
        if not file_id or not destination_folder_id:
            return {"error": True, "kind": "invalid_input",
                    "message": "move requires 'file_id' and 'destination_folder_id'"}
        return do_move(file_id, destination_folder_id)

    return {"error": True, "kind": "invalid_input",
            "message": f"Unknown operation: {operation}. Supported: create, move"}


# ============================================================================
# RESOURCES — Self-documenting MCP capabilities
# ============================================================================

@mcp.resource("mise://docs/overview")
def docs_overview() -> str:
    """Overview of mise-en-space MCP server."""
    return """# mise-en-space

Google Workspace MCP server with filesystem-first design.

## Tools (3 verbs)

| Tool | Purpose | Writes files? |
|------|---------|---------------|
| `search` | Find files/emails, deposit results to `mise/` | Yes |
| `fetch` | Download content to `mise/`, return path | Yes |
| `do` | Act on Workspace (create, move, rename, edit) | Varies |

## Sous-Chef Philosophy

When you fetch a doc/sheet/slides, open comments are automatically included
in the deposit as `comments.md`. The sous-chef brings everything you need
without being asked.

## Workflow

1. **Search** to find what you need
2. **Fetch** to download and extract content (includes open comments)
3. Read content from filesystem with standard tools
4. **Do** actions — create, move, rename, edit

## Content Types

Supported: **Web URLs**, Google Docs, Sheets, Slides, Gmail threads, PDFs, Office files, video/audio

## Resources

- `mise://docs/overview` — This overview
- `mise://docs/search` — Search tool details
- `mise://docs/fetch` — Fetch tool details and supported types
- `mise://docs/do` — Do tool details (create, move, rename, edit)
- `mise://docs/workspace` — Deposit folder structure
- `mise://docs/cross-source` — Cross-source search patterns (Drive↔Gmail linkage)
"""


@mcp.resource("mise://docs/search")
def docs_search() -> str:
    """Detailed documentation for the search tool."""
    return """# search

Search across Drive and Gmail. Deposits results to file for token efficiency.

## Filesystem-First Pattern

Search results are written to `mise/search--{query-slug}--{timestamp}.json`.
The tool returns the path and summary counts. Read the file for full results.

This pattern:
- Saves tokens (results don't bloat context)
- Scales to many parallel searches
- Lets you decide what to examine

## Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | str | required | Search terms |
| `sources` | list[str] | ['drive', 'gmail'] | Which sources to search |
| `max_results` | int | 20 | Maximum results per source |

## Examples

```python
# Search both sources
search("Q4 planning")
# Returns: {"path": "mise/search--q4-planning--2026-01-31T21-12-53.json",
#           "drive_count": 15, "gmail_count": 8, ...}

# Then read the file for full results
Read("mise/search--q4-planning--2026-01-31T21-12-53.json")
```

## Response Shape

```json
{
  "path": "mise/search--q4-planning--2026-01-31T21-12-53.json",
  "query": "Q4 planning",
  "sources": ["drive", "gmail"],
  "drive_count": 15,
  "gmail_count": 8
}
```

## Deposited File Shape

The JSON file contains the full results:

```json
{
  "query": "Q4 planning",
  "sources": ["drive", "gmail"],
  "drive_results": [
    {"id": "...", "name": "...", "mimeType": "...", "modified": "...", "url": "..."}
  ],
  "gmail_results": [
    {"thread_id": "...", "subject": "...", "snippet": "...", "from": "...", "date": "..."}
  ]
}
```

## Notes

- Drive search uses fullText contains (searches content, not just filename)
- Gmail search supports Gmail operators (from:, to:, subject:, after:, before:)
- Results are sorted by relevance (Google's ranking)
"""


@mcp.resource("mise://docs/fetch")
def docs_fetch() -> str:
    """Detailed documentation for the fetch tool."""
    return """# fetch

Fetch content to filesystem. Writes to `mise/` in current directory.

## Parameters

| Param | Type | Description |
|-------|------|-------------|
| `file_id` | str | Drive file ID, Gmail thread ID, or full URL |

## Supported Content Types

| Type | Output Format | Notes |
|------|---------------|-------|
| Web URLs | markdown | Clean article extraction, removes nav/ads/boilerplate (some protected sites may block) |
| Google Docs | markdown + comments.md | Multi-tab support, inline images, open comments |
| Google Sheets | CSV + comments.md | All sheets, with headers, open comments |
| Google Slides | markdown + thumbnails + comments.md | Selective thumbnails, open comments |
| Gmail threads | markdown | Signature stripping, attachment list |
| PDFs | markdown | Hybrid: markitdown → Drive fallback |
| DOCX/XLSX/PPTX | markdown/CSV | Via Drive conversion |
| Video/Audio | markdown + AI summary | Requires chrome-debug for summaries |

## Automatic Comment Enrichment

For Google Docs, Sheets, and Slides, open (unresolved) comments are automatically
fetched and deposited as `comments.md` alongside the content. This follows the
sous-chef philosophy: bring everything the chef needs without being asked.

The deposit folder will contain:
- `content.md` (or `content.csv` for Sheets)
- `comments.md` (if there are open comments)
- `manifest.json` (includes `open_comment_count`)

## Large File Handling

Files over 50MB use streaming downloads to avoid memory issues.
- Download streams directly to temp file
- Content extracted from disk, not memory
- Temp files cleaned up after extraction

This supports gigabyte-scale Office files (common at ITV).

## Response Shape

```json
{
  "path": "mise/doc--meeting-notes--abc123/",
  "content_file": "mise/doc--meeting-notes--abc123/content.md",
  "format": "markdown",
  "type": "doc",
  "metadata": {"title": "Meeting Notes", "mimeType": "..."}
}
```

## Auto-detection

The tool auto-detects input type:
- Web URLs (http/https not matching Google services below)
- Drive URLs (docs.google.com, sheets.google.com, slides.google.com, drive.google.com)
- Gmail URLs (mail.google.com)
- Gmail API IDs (16-character hex)
- Drive file IDs (default)

## Examples

```python
# Fetch web content (cleaner than curl/WebFetch)
fetch("https://simonwillison.net/2024/Dec/19/one-shot-python-tools/")

# GitHub raw files, APIs
fetch("https://raw.githubusercontent.com/fastapi/fastapi/master/pyproject.toml")

# Fetch by Google URL
fetch("https://docs.google.com/document/d/1abc.../edit")

# Fetch by ID
fetch("1abc...")

# Fetch Gmail thread
fetch("18f3a4b5c6d7e8f9")
```
"""


@mcp.resource("mise://docs/do")
def docs_do() -> str:
    """Detailed documentation for the do tool."""
    return """# do

Act on Google Workspace — create, move, rename, edit.

## Operations

| Operation | Description | Required params |
|-----------|-------------|-----------------|
| `create` | Create Doc/Sheet from content or deposit | `content`+`title` OR `source` |
| `move` | Move file to different folder | `file_id`, `destination_folder_id` |

More operations coming: overwrite, insert, rename.

## Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `operation` | str | 'create' | What to do |
| `content` | str | None | Inline content — markdown (doc) or CSV (sheet) |
| `title` | str | None | Document title (falls back to manifest title with source) |
| `doc_type` | str | 'doc' | 'doc', 'sheet', or 'slides' (for create) |
| `folder_id` | str | None | Destination folder ID (for create) |
| `source` | str | None | Path to deposit folder (reads content.md or content.csv) |
| `base_path` | str | None | Working directory for resolving relative source paths |
| `file_id` | str | None | Target file ID (for move) |
| `destination_folder_id` | str | None | Where to move the file (for move) |

## Deposit-Then-Publish (source param)

Instead of passing content inline, write it to a `mise/` deposit folder and pass the path:

```python
# 1. Claude writes content to disk (cheap)
# 2. Human inspects, edits if needed
# 3. Publish from deposit (15 tokens vs 5000 for inline CSV)
do(operation="create", source="mise/sheet--q4-analysis--draft/", base_path="/path/to/project")
```

Title falls back to `manifest.json` title if not passed explicitly.
After creation, manifest.json is enriched with `status`, `file_id`, `web_link`, `created_at`.

## Response Shape (create)

```json
{
  "file_id": "1abc...",
  "web_link": "https://docs.google.com/document/d/1abc.../edit",
  "title": "My Document",
  "type": "doc"
}
```

## Response Shape (move)

```json
{
  "file_id": "1abc...",
  "title": "Moved File",
  "web_link": "https://drive.google.com/...",
  "operation": "move",
  "cues": {
    "destination_folder": "Archive",
    "destination_folder_id": "1xyz...",
    "previous_parents": ["0old..."]
  }
}
```

## Markdown Conversion (create)

Google's native markdown import handles:
- Headings (H1-H6)
- Bold, italic, strikethrough
- Lists (ordered, unordered, nested)
- Links
- Tables
- Code blocks
- Task lists (`- [ ]` and `- [x]`)

## Examples

```python
# Create a document (inline content)
do(operation="create", content="# Meeting Notes\\n\\n- Item 1", title="Team Sync")

# Create from deposit folder (deposit-then-publish)
do(operation="create", source="mise/sheet--q4-analysis--draft/", title="Q4 Analysis", doc_type="sheet", base_path="/path/to/project")

# Create in specific folder
do(operation="create", content="| A | B |\\n|---|---|", title="Data", doc_type="sheet", folder_id="1xyz...")

# Move a file to a different folder
do(operation="move", file_id="1abc...", destination_folder_id="1xyz...")
```
"""


@mcp.resource("mise://docs/cross-source")
def docs_cross_source() -> str:
    """Documentation for cross-source search patterns."""
    return """# Cross-Source Search Patterns

When exploring context, you often need to bounce between sources:

- **Drive → Email**: Found a file, want the email thread that sent it
- **Email → Drive**: Found an email, want to read the attachments/linked files

## Direction 1: Drive → Email

### Pattern A: Search by filename

When you find a file in Drive, search Gmail for the email that shared it:

```python
# Found in Drive search
{"name": "xgbtest.R", "id": "abc123..."}

# Search Gmail for emails with that attachment
search("filename:xgbtest.R", sources=["gmail"])
```

The `filename:` operator searches attachment names.

### Pattern B: Files from "Email Attachments" folder

The user may have an exfiltration script that copies email attachments to Drive
for fulltext indexing. These files have email metadata in their description:

```
From: alice@example.com
Subject: Budget analysis
Date: 2026-01-15T10:30:00Z
Message ID: 18f4a5b6c7d8e9f0
Content Hash: abc123...
```

If you see a file in "Email Attachments" folder, the **Message ID** can be
used to fetch the source email thread:

```python
fetch("18f4a5b6c7d8e9f0")  # Returns the email thread
```

## Direction 2: Email → Drive

### Following attachments

When you fetch an email thread, attachments are listed in the markdown:

```markdown
**Attachments:**
- budget_v3.xlsx (application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, 1.2 MB)
- notes.pdf (application/pdf, 450 KB)
```

To find these in Drive (if exfiltrated):

```python
search("name contains 'budget_v3.xlsx'", sources=["drive"])
```

### Following Drive links

Emails often contain Drive links instead of attachments. These are also listed:

```markdown
**Linked files:**
- [1abc...](https://docs.google.com/document/d/1abc...)
```

Fetch directly by ID:

```python
fetch("1abc...")  # Works with file IDs from links
```

## The Exploration Loop

Context exploration often involves iterating:

1. Search Drive for topic → find file
2. Search Gmail `filename:X` → find email thread with context
3. Read email → discover new terms, people, related files
4. Search Drive with new terms → repeat

This loop discovers the **meaning** (in communications) behind **artifacts** (files).

## Gmail Search Operators

Useful operators for cross-source exploration:

| Operator | Example | Finds |
|----------|---------|-------|
| `filename:` | `filename:report.pdf` | Emails with attachment named report.pdf |
| `has:attachment` | `has:attachment budget` | Emails about budget with any attachment |
| `from:` | `from:alice@example.com` | Emails from Alice |
| `to:` | `to:team@company.com` | Emails to the team |
| `after:` | `after:2026/01/01` | Emails after Jan 1, 2026 |
| `before:` | `before:2026/02/01` | Emails before Feb 1, 2026 |
"""


@mcp.resource("mise://docs/workspace")
def docs_workspace() -> str:
    """Documentation for the workspace/deposit folder structure."""
    return """# Workspace Deposit Structure

Fetched content goes to `mise/` in the current working directory.

## Folder Structure

```
mise/
├── doc--meeting-notes--abc123/
│   ├── manifest.json
│   └── content.md
├── slides--q4-deck--xyz789/
│   ├── manifest.json
│   ├── content.md
│   ├── slide_01.png
│   ├── slide_02.png
│   └── ...
├── sheet--budget--def456/
│   ├── manifest.json
│   └── content.csv
└── gmail--re-project--thread123/
    ├── manifest.json
    └── content.md
```

## Folder Naming

`{type}--{title-slug}--{id-prefix}/`

- **type**: slides, doc, sheet, gmail, pdf, docx, xlsx, pptx, video
- **title-slug**: Slugified title, max 50 chars
- **id-prefix**: First 12 characters of resource ID

## manifest.json

Self-describing metadata for each deposit:

```json
{
  "type": "slides",
  "title": "Q4 Planning Deck",
  "id": "1OepZjuwi2emuHPAP...",
  "fetched_at": "2026-01-25T17:00:00+00:00",
  "slide_count": 43,
  "has_thumbnails": true,
  "thumbnail_count": 12
}
```

## Content Files

| Type | File | Format |
|------|------|--------|
| Docs, Slides, Gmail, PDF, Video | content.md | Markdown |
| Sheets, XLSX | content.csv | CSV |
| PPTX | content.txt | Plain text |

## Thumbnails

Slides get selective thumbnails — only fetched for:
- Charts (visual IS the content)
- Images (unless single large image = stock photo)
- Fragmented text (≥5 short pieces, layout matters)

Text-only slides and stock photos are skipped.
"""


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
    """Handle termination signals by exiting cleanly."""
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)
    mcp.run()
