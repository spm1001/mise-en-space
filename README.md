# mise-en-space

Google Workspace MCP server — *mise en place* for knowledge work. Everything prepped, in its place, ready for Claude to cook with.

## Why

We tested every Google Workspace MCP we could find. The official one has 50 tools and requires multiple round-trips for basic tasks — search Gmail, get back a list of IDs, call again for each message. Our v1 had rich metadata but messy architecture and 21 tools eating ~17k tokens of context just by existing.

mise-en-space is what came out of that bakeoff:

- **3 tools, not 50.** Search, fetch, create. ~3k tokens of tool definitions. Everything routes through the same three verbs.
- **Filesystem-first.** Content goes to disk as markdown/CSV, not into the context window. Claude reads what it needs.
- **Sous-chef philosophy.** Fetch a doc and get the comments too. Fetch an email and get the attachments extracted. Don't make the chef ask for every ingredient separately.
- **One call, rich results.** Gmail search returns subjects, senders, snippets, and attachment names — not a bag of IDs requiring N+1 follow-ups.
- **Clean extraction.** Web pages arrive as clean markdown without nav bars. PDFs use hybrid extraction (markitdown → Drive OCR fallback). Office files convert automatically.

## The 3 Verbs

| Verb | Purpose | Deposits files? |
|------|---------|-----------------|
| `search` | Find files and emails across Drive and Gmail | Yes — results JSON |
| `fetch` | Extract content to `mise-fetch/` as markdown/CSV | Yes — content folder |
| `create` | Make a new Doc/Sheet/Slides from markdown | No |

## Supported Content Types

| Source | What you get |
|--------|-------------|
| Google Docs | Markdown + open comments |
| Google Sheets | CSV + chart PNGs + open comments |
| Google Slides | Markdown + selective thumbnails + open comments |
| Gmail threads | Markdown with signature stripping, attachment extraction |
| PDFs | Markdown (markitdown → Drive OCR fallback) |
| Office files (DOCX/XLSX/PPTX) | Markdown or CSV via Drive conversion |
| Web URLs | Clean article extraction, JS rendering fallback |
| Video/Audio | AI summary + metadata (requires chrome-debug) |
| Images | Deposited as-is; SVG rendered to PNG |

## Setup

### 1. Clone and install

```bash
git clone https://github.com/spm1001/mise-en-space.git
cd mise-en-space
uv sync
```

### 2. Google OAuth

mise-en-space uses `itv-google-auth` (private package) for OAuth. You need a Google Cloud project with the Drive, Gmail, and Slides APIs enabled.

```bash
# First run will trigger OAuth flow
uv run python cli.py search "test"
```

### 3. Add to Claude as MCP server

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "mise": {
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "/path/to/mise-en-space", "run", "python", "server.py"]
    }
  }
}
```

### 4. Link the skill (recommended)

The `skill/` directory contains a Claude skill that teaches Claude *how* to use mise effectively — Gmail operators, the exploration loop, comment checking patterns.

```bash
# For Claude Code
ln -s /path/to/mise-en-space/skill ~/.claude/skills/mise

# For pi
ln -s /path/to/mise-en-space/skill ~/.pi/agent/skills/mise
```

Without the skill, Claude can call the tools but won't know the patterns that make them useful (like following `email_context` hints or filtering large results with jq).

## CLI

Same 3 verbs, for agents without MCP support:

```bash
mise search "quarterly reports"
mise search "from:alice budget" --sources gmail
mise fetch 1abc123def456
mise fetch "https://simonwillison.net/..."
mise create "Title" --content "# Markdown content"
```

## Architecture

```
server.py       MCP server (thin wrappers around tools)
cli.py          CLI interface (same verbs, same tools)
tools/          Business logic — routing, orchestration
adapters/       Thin Google API wrappers (one per service)
extractors/     Pure functions, no I/O (testable without APIs)
workspace/      File deposit management
skill/          Claude skill (symlinked to ~/.claude/skills/mise)
```

**Layer rules:**
- Extractors never import from adapters (no I/O)
- Tools wire adapters → extractors → workspace
- Server and CLI are both thin wrappers around tools

Adding a new content type means: adapter (API call), extractor (parse), tool (wire + deposit). The layers are independent.

## Design Decisions

**Why filesystem-first?** A 70-slide presentation or 30-message Gmail thread can be 50k+ tokens. Dumping that into the MCP response wastes context. Writing to disk lets Claude read selectively — `head -50`, `grep`, or read the whole thing.

**Why 3 tools?** Every MCP tool definition costs tokens just by existing. 50 tools × ~300 tokens each = 15k tokens before Claude does anything. 3 tools × ~1k each = 3k tokens. The routing happens server-side.

**Why sous-chef?** When you fetch a document, you almost always want the comments too. When you fetch an email, you want the attachments extracted. Requiring separate calls for predictable follow-ups wastes round-trips.

**Why `base_path`?** MCP servers run as separate processes. Without `base_path`, deposits land in the server's directory, not Claude's working directory. Always pass `base_path` when calling via MCP.
