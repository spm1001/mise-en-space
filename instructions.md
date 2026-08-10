# Mise — Instruction Shard

Auto-loaded from `~/.claude/rules/`, rewritten each session by this plugin's
`hooks/ensure-mise.sh` — edit that or this file, never the copy in `rules/`.

## Overrides

| Your Default | What I Need |
|-------------|-------------|
| WebFetch for Google Workspace | `mise fetch` for Google Drive, Gmail, Slides — it handles auth and format conversion |

## Google Drive API (raw)

**Folder creation is NOT a reason to bypass mise** — `do(operation="create", doc_type="folder", title="…")` mints a Drive folder natively and sets `supportsAllDrives` for you. *(Corrected 2026-08-10, mise-kagejo: this row used folder creation as its worked example of bypassing, and it cost a real session a hand-rolled PEP 723 Drive-API script it didn't need — the session loaded the `do()` schema minutes later and found `doc_type` had accepted `folder` all along.)*

If you do bypass mise for something it genuinely can't do, pass `supportsAllDrives=true` — Shared Drive files are invisible to the raw Drive API without it.
