# Mise — Instruction Shard

Auto-loaded via `~/.claude/rules/mise.md`.

## Overrides

| Your Default | What I Need |
|-------------|-------------|
| WebFetch for Google Workspace | `mise fetch` for Google Drive, Gmail, Slides — it handles auth and format conversion |

## Google Drive API (raw)

When bypassing mise (e.g. folder creation), pass `supportsAllDrives=true` — Shared Drive files are invisible without it. Mise handles this automatically.
