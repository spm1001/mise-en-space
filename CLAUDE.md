# CLAUDE.md

Google Workspace MCP v2 — strangler fig replacement for mcp-google-workspace.

## What This Is

A complete rewrite of the Google Workspace MCP with:
- **Filesystem-first design** — content to disk, caller controls ingestion
- **Clean layer separation** — extractors → adapters → tools
- **Minimal verb surface** — search, fetch, create, help
- **Token efficiency** — dense output, trimmed fluff

## Architecture

```
extractors/     Pure functions, no MCP awareness (testable without APIs)
adapters/       Thin Google API wrappers (easily mocked)
tools/          MCP tool definitions (thin wiring layer)
workspace/      Per-session folder management (~/.mcp-workspace/)
server.py       FastMCP entry point
```

**Layer rules:**
- Extractors NEVER import from adapters or tools
- Adapters NEVER import from tools
- Tools wire adapters → extractors → workspace
- server.py just registers tools

## Development

```bash
uv sync                     # Install dependencies
uv run python server.py     # Run MCP server
uv run pytest               # Run tests
uv run pytest tests/unit    # Unit tests only (fast, mocked)
```

## Porting from v1

Battle-tested extractors to port from `mcp-google-workspace`:
- `docs.py` → `extractors/docs.py`
- `sheets.py` → `extractors/sheets.py`
- `slides.py` → `extractors/slides.py`
- `gmail.py` → `extractors/gmail.py` (signature stripping logic)

**Key change:** v1 extractors call Google APIs directly. v2 extractors are pure functions that receive API response data and return processed content.

## OAuth

Credentials are symlinked from v1 (shared OAuth):
- `credentials.json` → `../mcp-google-workspace/credentials.json`
- `token.json` → `../mcp-google-workspace/token.json`

To re-authenticate: `cd ../mcp-google-workspace && uv run python -m workspace_mcp.auth`

## File Deposit Structure

```
~/.mcp-workspace/
├── [account@domain.com]/
│   ├── drive/{fileId}.md        # Fetched files
│   ├── gmail/{threadId}.txt     # Fetched threads
│   └── attachments/             # Downloaded attachments
└── temp/                        # Auto-cleanup
```

## Related

- `mcp-google-workspace` — v1 (source for porting)
- `mcp-google-workspace/docs/V2.md` — Authoritative spec
- Bead: `mcp-google-workspace-awq` — tracks v2 epic
