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

from typing import Any


@mcp.tool()
def search(
    query: str,
    sources: list[str] | None = None,
    max_results: int = 20
) -> dict[str, Any]:
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
def fetch(file_id: str) -> dict[str, Any]:
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
    folder_id: str | None = None
) -> dict[str, Any]:
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
| `search` | Find files/emails, return metadata + snippets | No |
| `fetch` | Download content to `mise-fetch/`, return path | Yes |
| `create` | Make new Doc/Sheet/Slides from markdown | No |

## Workflow

1. **Search** to find what you need
2. **Fetch** to download and extract content
3. Read content from filesystem with standard tools
4. **Create** new documents when needed

## Content Types

Supported: Google Docs, Sheets, Slides, Gmail threads, PDFs, Office files (DOCX/XLSX/PPTX), video/audio

## Resources

- `mise://docs/overview` — This overview
- `mise://docs/search` — Search tool details
- `mise://docs/fetch` — Fetch tool details and supported types
- `mise://docs/create` — Create tool details
- `mise://docs/workspace` — Deposit folder structure
"""


@mcp.resource("mise://docs/search")
def docs_search() -> str:
    """Detailed documentation for the search tool."""
    return """# search

Search across Drive and Gmail. Returns metadata + snippets for triage.

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

# Search Drive only
search("budget 2026", sources=["drive"])

# Search Gmail only
search("from:boss@company.com", sources=["gmail"])
```

## Response Shape

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

Fetch content to filesystem. Writes to `mise-fetch/` in current directory.

## Parameters

| Param | Type | Description |
|-------|------|-------------|
| `file_id` | str | Drive file ID, Gmail thread ID, or full URL |

## Supported Content Types

| Type | Output Format | Notes |
|------|---------------|-------|
| Google Docs | markdown | Multi-tab support, inline images |
| Google Sheets | CSV | All sheets, with headers |
| Google Slides | markdown + PNG thumbnails | Selective thumbnails (charts, images, complex layouts) |
| Gmail threads | markdown | Signature stripping, attachment list |
| PDFs | markdown | Hybrid: markitdown → Drive fallback |
| DOCX/XLSX/PPTX | markdown/CSV | Via Drive conversion |
| Video/Audio | markdown + AI summary | Requires chrome-debug for summaries |

## Large File Handling

Files over 50MB use streaming downloads to avoid memory issues.
- Download streams directly to temp file
- Content extracted from disk, not memory
- Temp files cleaned up after extraction

This supports gigabyte-scale Office files (common at ITV).

## Response Shape

```json
{
  "path": "mise-fetch/doc--meeting-notes--abc123/",
  "content_file": "mise-fetch/doc--meeting-notes--abc123/content.md",
  "format": "markdown",
  "type": "doc",
  "metadata": {"title": "Meeting Notes", "mimeType": "..."}
}
```

## Auto-detection

The tool auto-detects ID type:
- Drive URLs (docs.google.com, sheets.google.com, slides.google.com, drive.google.com)
- Gmail URLs (mail.google.com)
- Gmail API IDs (16-character hex)
- Drive file IDs (default)

## Examples

```python
# Fetch by URL
fetch("https://docs.google.com/document/d/1abc.../edit")

# Fetch by ID
fetch("1abc...")

# Fetch Gmail thread
fetch("18f3a4b5c6d7e8f9")
```
"""


@mcp.resource("mise://docs/create")
def docs_create() -> str:
    """Detailed documentation for the create tool."""
    return """# create

Create Google Workspace document from markdown content.

## Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `content` | str | required | Markdown content |
| `title` | str | required | Document title |
| `doc_type` | str | 'doc' | 'doc', 'sheet', or 'slides' |
| `folder_id` | str | None | Destination folder ID |

## Response Shape

```json
{
  "file_id": "1abc...",
  "web_link": "https://docs.google.com/document/d/1abc.../edit",
  "title": "My Document",
  "type": "doc"
}
```

## Markdown Conversion

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
# Create a document
create("# Meeting Notes\\n\\n- Item 1\\n- Item 2", title="Team Sync")

# Create in specific folder
create("| A | B |\\n|---|---|\\n| 1 | 2 |", title="Data", doc_type="sheet", folder_id="1xyz...")
```
"""


@mcp.resource("mise://docs/workspace")
def docs_workspace() -> str:
    """Documentation for the workspace/deposit folder structure."""
    return """# Workspace Deposit Structure

Fetched content goes to `mise-fetch/` in the current working directory.

## Folder Structure

```
mise-fetch/
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
# SERVER ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    mcp.run()
