# Agent Instructions

**mise-en-space** — Content fetching for Google Workspace (web URLs, Google Drive, Gmail).

## Quick Reference

```bash
# CLI (for pi and other non-MCP agents)
mise search "quarterly reports"
mise search "from:alice budget" --sources gmail
mise fetch 1abc123def456
mise fetch "https://simonwillison.net/..."
mise create "Title" --content "# Markdown"

# Development
uv run pytest               # Run tests
uv run python server.py     # Run MCP server
```

## Work Tracking

This project uses **arc** for issue tracking.

```bash
arc list --ready            # Find available work
arc show <id>               # View issue details
arc done <id>               # Complete work
```

## Architecture

```
adapters/       Google API wrappers (thin, async)
extractors/     Pure functions (no I/O, testable)
tools/          Tool implementations (business logic)
workspace/      File deposit management
server.py       MCP server (thin wrappers)
cli.py          CLI interface (pi-compatible)
```

**Layer rules:**
- Extractors NEVER import from adapters (no I/O)
- Adapters MAY import parsing utilities from extractors
- Tools wire adapters → extractors → workspace

## CLI vs MCP

Both provide the same 3 verbs:

| Verb | CLI | MCP |
|------|-----|-----|
| search | `mise search "query"` | `mcp__mise__search(query)` |
| fetch | `mise fetch <id>` | `mcp__mise__fetch(file_id)` |
| create | `mise create "Title"` | `mcp__mise__create(content, title)` |

The CLI is for agents without MCP support (like pi). Same functionality, different invocation.

## Session Completion

**When ending a work session:**

1. Create issues for remaining work
2. Run quality gates (if code changed): `uv run pytest`
3. Update issue status
4. Push to remote:
   ```bash
   git pull --rebase && git push
   ```
5. Verify: `git status` shows "up to date"
