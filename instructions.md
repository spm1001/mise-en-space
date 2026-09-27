# Mise — Instruction Shard

Auto-loaded from `~/.claude/rules/`, rewritten each session by this plugin's
`hooks/ensure-mise.sh` — edit that or this file, never the copy in `rules/`.

## Overrides

| Your Default | What I Need |
|-------------|-------------|
| WebFetch for Google Workspace | `mise fetch` for Google Drive, Gmail, Slides — it handles auth and format conversion |

## Google Drive API (raw)

**Folder creation is NOT a reason to bypass mise** — `do(operation="create", doc_type="folder", title="…")` mints a Drive folder natively and sets `supportsAllDrives` for you.

If you do bypass mise for something it genuinely can't do, pass `supportsAllDrives=true` — Shared Drive files are invisible to the raw Drive API without it.
