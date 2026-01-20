# mcp-workspace-v2

Google Workspace MCP v2 — Strangler fig replacement for mcp-google-workspace.

## Architecture

```
extractors/     Pure functions, no MCP awareness (testable without APIs)
adapters/       Thin Google API wrappers (easily mocked)
tools/          MCP tool definitions (thin wiring layer)
workspace/      Per-session folder management
tests/unit/     Extractors, adapters (mocked)
tests/integration/  Real API calls
fixtures/       Test file IDs, expected outputs
```

## Verb Model

| Verb | Purpose |
|------|---------|
| **search** | Unified discovery across Drive/Gmail/Contacts |
| **fetch** | Content to filesystem, return path |
| **create** | Markdown → Doc/Sheet/Slides |
| **help** | Self-documentation |

## Key Decisions

- **Filesystem-first:** Content goes to disk, caller controls ingestion
- **Per-session working folder:** Persists for multi-session work
- **Drive = canonical doc surface:** Gmail attachments exfiltrated to Drive
- **Claude-only caller:** No multi-user OAuth complexity

## Related

- `mcp-google-workspace` — v1 (being replaced)
- Bead: `mcp-google-workspace-awq` — tracks v2 epic
