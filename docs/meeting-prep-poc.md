# Field Report: Meeting Prep Proof of Concept

**Date:** 2026-03-05
**Context:** Exploring Google Workspace CLI (gws), we built a proof-of-concept meeting prep for an Annalect meeting and discovered what a real standup/prep workflow needs.

## What gws Taught Us

`gws workflow +standup-report` returns calendar events + tasks as JSON in ~0.6s. It's fast but shallow — just a reformatted calendar. The gap between "list of meetings" and "actionable prep" is where all the value lives.

gws is installed at `~/.local/npm-global/bin/gws` (v0.4.1). It can use our existing OAuth token via `GOOGLE_WORKSPACE_CLI_TOKEN` env var — refresh with `uv run python3 -c "..."` from mise-en-space's venv. Useful for calendar reads since mise doesn't have a calendar adapter.

## The Exploration Loop for Meeting Prep

The PoC followed this pattern, which a future feature should automate:

1. **Calendar event** (via gws) → attendees, description, linked docs, room
2. **Drive search** for topic keywords → recent files, shared docs
3. **Gmail search** for recent threads with attendees (`from:` operator + `after:` date)
4. **Fetch key docs** → content + comments + cues
5. **Follow breadcrumbs** — email_context in fetched docs, "did you see my email about X" references, Drive links in email bodies
6. **Compose** — synthesize into attendees, open threads, suggested agenda, flags

## What Surfaced That a Calendar Alone Can't

For the Annalect meeting, the loop found:

- **Jon Fox is coming in person** (from yesterday's email) despite not being on the calendar invite
- **Privacy/legal thread stuck for 8 days** — forwarded to Ella but lawyers never connected
- **The 13:30 legal sync with Ella is right after Annalect** — actionable timing
- **SOW from August may have gone cold** — no recent mention
- **Geo experimentation draft** exists as a separate negotiation track
- **Lauren declined** — visible in calendar but easy to miss without the attendee table

## Architecture Considerations

### Calendar Access

mise-en-space has `calendar.readonly` scope in its token already. Two options:
1. **Shell out to gws** — `gws calendar events list --params '{...}'` with GOOGLE_WORKSPACE_CLI_TOKEN. Fast, no new adapter needed, but adds a binary dependency.
2. **Build a calendar adapter** — consistent with mise architecture. More work but no external dependency.

Recommendation: Start with gws shelling for the PoC, consider a proper adapter if it becomes a core feature. The calendar data is metadata-only (no content extraction needed), so the adapter would be thin.

### What "Meeting Prep" Means as a Feature

It's NOT a new MCP tool. It's a **workflow pattern** that composes existing tools:
- `gws` (or future calendar adapter) for event data
- `mise search` for Drive + Gmail
- `mise fetch` for content extraction
- `mise do(operation="create")` to deposit the prep doc

The intelligence is in the **composition logic**: which searches to run, what breadcrumbs to follow, when to stop. This could live as:
- A **skill** that teaches Claude the pattern (lowest effort, highest flexibility)
- A **script** that automates the mechanical parts (search fanout, attendee extraction)
- A **tool** in mise that wraps the whole workflow (highest effort, least flexible)

Skill-first is probably right. The PoC showed that the exploration loop needs judgment calls (which threads are substantive vs logistics, when to stop searching). That's Claude's strength.

### Token Budget

The PoC consumed significant context:
- Calendar event JSON: ~3K tokens
- Email threads (3 fetched): ~4K tokens
- Gemini transcript (1 doc): ~12K tokens (mostly noise — the "cleanroom kickoff" was actually a casual chat)
- Draft doc: ~500 tokens
- Search results (5 searches): ~2K tokens

Total: ~22K tokens of input before composing the output. A subagent pattern would protect the main context — delegate the research, return a summary.

## Speed

| Step | Time |
|------|------|
| gws calendar query | ~0.5s |
| mise search (Drive) | ~2s |
| mise search (Gmail) | ~2s |
| mise fetch (email thread) | ~2-3s each |
| mise fetch (doc) | ~3-5s each |
| Total for Annalect prep | ~30s of API time |

Acceptable for a prep workflow that runs once before a meeting.
