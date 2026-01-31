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

from tools import do_search, do_search_activity, do_fetch, do_fetch_comments, do_create

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


@mcp.tool()
def fetch_comments(
    file_id: str,
    include_deleted: bool = False,
    include_resolved: bool = True,
    max_results: int = 100
) -> dict[str, Any]:
    """
    Fetch comments from a Drive file.

    Returns comments as formatted markdown. Includes comment text,
    author (name and email), quoted anchor text, @mentions, and threaded replies.

    Args:
        file_id: Drive file ID or URL
        include_deleted: Include deleted comments (default: False)
        include_resolved: Include resolved comments (default: True).
            Set to False to get only unresolved/open comments needing attention.
        max_results: Maximum comments to return (default: 100)

    Returns:
        content: Formatted markdown with all comments
        file_id: The file ID
        file_name: The file name
        comment_count: Number of comments found
        warnings: Any extraction issues

    Notes:
        - Google Docs have human-readable anchor text (what was highlighted)
        - DOCX/Sheets anchors are opaque (empty in output)
        - Forms, Shortcuts, Sites don't support comments (returns error)
        - @mentions are captured for action item detection
    """
    return do_fetch_comments(file_id, include_deleted, include_resolved, max_results)


@mcp.tool()
def search_activity(
    filter_type: str = "comments",
    file_id: str | None = None,
    max_results: int = 50
) -> dict[str, Any]:
    """
    Search recent activity across your Drive.

    Useful for finding:
    - Action items (comments mentioning you)
    - Recent discussions on your files
    - Activity history for a specific file

    Args:
        filter_type: Type of activity to find:
            - "comments": Comment activities (default)
            - "edits": Edit activities (requires file_id)
            - "all": All activities (requires file_id)
        file_id: If provided, get activity for this specific file only.
            Required for filter_type other than "comments".
        max_results: Maximum activities to return (default: 50)

    Returns:
        activities: List of activity objects with:
            - timestamp, actor, action_type
            - target (file_id, file_name, web_link)
            - mentioned_users (for comments)
        activity_count: Number of activities returned
        next_page_token: For pagination if more results exist
    """
    return do_search_activity(filter_type, file_id, max_results)


# ============================================================================
# RESOURCES — Self-documenting MCP capabilities
# ============================================================================

@mcp.resource("mise://docs/overview")
def docs_overview() -> str:
    """Overview of mise-en-space MCP server."""
    return """# mise-en-space

Google Workspace MCP server with filesystem-first design.

## Tools (5 verbs)

| Tool | Purpose | Writes files? |
|------|---------|---------------|
| `search` | Find files/emails, return metadata + snippets | No |
| `fetch` | Download content to `mise-fetch/`, return path | Yes |
| `fetch_comments` | Get comments from a file as markdown | No |
| `search_activity` | Find recent activity (comments, edits) across files | No |
| `create` | Make new Doc/Sheet/Slides from markdown | No |

## Workflow

1. **Search** to find what you need
2. **Fetch** to download and extract content
3. Read content from filesystem with standard tools
4. **Create** new documents when needed

## Finding Action Items

Use `search_activity` to find comments mentioning you across all files, or
`fetch_comments` with `include_resolved=False` to see open comments on a specific file.

## Content Types

Supported: Google Docs, Sheets, Slides, Gmail threads, PDFs, Office files (DOCX/XLSX/PPTX), video/audio

## Resources

- `mise://docs/overview` — This overview
- `mise://docs/search` — Search tool details
- `mise://docs/fetch` — Fetch tool details and supported types
- `mise://docs/fetch-comments` — Fetch comments tool details
- `mise://docs/search-activity` — Activity search tool details
- `mise://docs/create` — Create tool details
- `mise://docs/workspace` — Deposit folder structure
- `mise://docs/cross-source` — Cross-source search patterns (Drive↔Gmail linkage)
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


@mcp.resource("mise://docs/fetch-comments")
def docs_fetch_comments() -> str:
    """Detailed documentation for the fetch_comments tool."""
    return """# fetch_comments

Fetch comments from a Drive file. Returns formatted markdown directly.

## Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `file_id` | str | required | Drive file ID or URL |
| `include_deleted` | bool | False | Include deleted comments |
| `include_resolved` | bool | True | Include resolved comments. Set False for open comments only. |
| `max_results` | int | 100 | Maximum comments to return |

## Response Shape

```json
{
  "content": "## Comments on \\"Doc Title\\" (3 total)\\n\\n### [Author]...",
  "file_id": "1abc...",
  "file_name": "Document Title",
  "comment_count": 3,
  "warnings": null
}
```

## Output Format

The `content` field contains markdown:

```markdown
## Comments on "Document Title" (3 total)

### [Alice Smith <alice@example.com>] • 2026-01-15
*Mentions: @bob@example.com, @carol@example.com*

> Quoted text from document

Comment content here.

**Replies:**
- **[Bob Jones]** (2026-01-16): Reply text here.

---

### [Carol White] • 2026-01-17
...
```

## Finding Action Items

```python
# Get only unresolved (open) comments
fetch_comments("1abc...", include_resolved=False)
```

This is useful for finding what needs attention on a specific document.

## Anchor Text (Quoted Context)

- **Google Docs**: Human-readable quoted text (what was highlighted)
- **DOCX/Sheets**: Empty (anchors are opaque coordinates)

## Unsupported File Types

These file types don't support comments and return an error:
- Google Forms
- Shortcuts (doesn't resolve to target)
- Sites, Maps, Apps Script

Folders return 0 comments (no error).

## Examples

```python
# Get comments from a doc
fetch_comments("1abc...")

# Get comments from a URL
fetch_comments("https://docs.google.com/document/d/1abc.../edit")

# Get only unresolved comments (action items)
fetch_comments("1abc...", include_resolved=False)

# Include deleted comments
fetch_comments("1abc...", include_deleted=True)
```
"""


@mcp.resource("mise://docs/search-activity")
def docs_search_activity() -> str:
    """Detailed documentation for the search_activity tool."""
    return """# search_activity

Search recent activity across your Drive. Find action items and track changes.

## Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `filter_type` | str | "comments" | Type of activity: "comments", "edits", "all" |
| `file_id` | str | None | Specific file to get activity for |
| `max_results` | int | 50 | Maximum activities to return |

## Use Cases

### Finding Action Items

```python
# Get recent comments across all your files
search_activity()

# Get only comment activities (default)
search_activity(filter_type="comments")
```

### File Activity History

```python
# Get all activity for a specific file
search_activity(file_id="1abc...", filter_type="all")

# Get just edits for a file
search_activity(file_id="1abc...", filter_type="edits")
```

## Response Shape

```json
{
  "activities": [
    {
      "activity_id": "...",
      "timestamp": "2026-01-20T10:30:00Z",
      "actor": {"name": "Alice Smith", "email": null},
      "target": {
        "file_id": "1abc...",
        "file_name": "Q4 Planning",
        "mime_type": "application/vnd.google-apps.document",
        "web_link": "https://docs.google.com/document/d/1abc.../edit"
      },
      "action_type": "comment",
      "mentioned_users": ["Bob Jones"]
    }
  ],
  "activity_count": 1,
  "next_page_token": null
}
```

## Action Types

| Type | Meaning |
|------|---------|
| `comment` | New comment added |
| `reply` | Reply to existing comment |
| `resolve` | Comment marked resolved |
| `reopen` | Resolved comment reopened |
| `assign` | Task assigned in comment |
| `suggest` | Suggestion made |
| `edit` | File content edited |
| `create` | File created |

## Notes

- Cross-file search only supports `filter_type="comments"`
- For edits/all activity, you must provide a `file_id`
- Actor emails are not exposed due to privacy (only names)
- `mentioned_users` shows who was @mentioned in comments
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
