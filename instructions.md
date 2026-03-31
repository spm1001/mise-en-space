# Mise — Instruction Shard

Auto-loaded via `~/.claude/rules/mise.md`.

## Overrides

| Your Default | What I Need |
|-------------|-------------|
| WebFetch for Google Workspace | `mise fetch` for Google Drive, Gmail, Slides. Always. |

## Google Drive API (raw)

When bypassing mise (e.g. folder creation), always pass `supportsAllDrives=true` — Shared Drive files are invisible without it. Mise handles this automatically.
