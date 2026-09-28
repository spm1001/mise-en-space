# Calendar confirm dialog, seen live (mise-pukiri step 4, 2026-09-28)

Two hublot-driven interactive Claude Code sessions (CC 2.1.283, Opus 5.5 on Vertex via `claudev`, bypass-permissions mode, cwd `~/repos/spm1001/deglacer`, `--mcp-config mcp-config.json` registering THIS clone as `miseclone`), run overnight by a dispatched worker with nobody at the keyboard. The driver answered every dialog with keypresses. Every call was `do(create_event)` on Sameer's own ITV calendar, Sun 4 Oct 2026 04:00–04:15 (a slot checked empty across all eight calendars in his list beforehand), with the account's own address as the only attendee and `send_updates='none'`, so no invite could leave the account.

## What the first session showed: the first-cut message did not fit the dialog

The first session ran the first cut (a344c06), whose preview `message` put everything on long lines.

| File | What it shows |
|---|---|
| `pane-1-dialog-first-cut-clipped.txt` | The dialog renders: *MCP server "miseclone" requests your input*, then the message. The first line is **clipped with "…"** at the pane width: the tail of the email wording never reaches the screen. |
| `pane-2-decline-result.txt` | Down → Right → Enter (Decline). Nothing booked; `confirm_gate: "elicitation: declined — … Nothing was booked and nobody was emailed…"`, no `confirm_required`. |
| `pane-3-dialog-four-line-cap.txt` | A recurring call with a long location. Line 3 is clipped too, and after 3 lines the rest collapses to **`… (+3 more lines)`**. The hidden lines were the clash check, the clash caveat ("Clash check covers the FIRST instance only.") and a stray-instance warning. Up and Ctrl-O do not expand it. |
| `pane-4-escape-cancel-result.txt` | Esc (cancel). Nothing booked; `confirm_gate: "elicitation: cancel — …the confirm= round-trip applies…"`, `confirm_required` kept. |

The limits come from the CC bundle, not from one render. The dialog passes the message through `tXe(text, width, maxLines = t2 + 1)` with `t2 = 3`. That function clips every line (it never wraps) to `max(20, width − tf)`, where `tf = max(2*zA, 2*(aU+iU)) = 6`. A message over 4 lines shows its first 3 lines plus `… (+N more lines)`. The capture agrees exactly: 193 visible columns in a 199-column pane. On an 80-column terminal that budget is **4 lines of 74 columns**.

## What changed (e3e4a51), and what the second session showed

`tools/elicit.py::confirm_marker` now asks only when the whole message fits 4 lines of 74 columns (`fits_dialog`). Anything longer takes the confirm= round-trip, where the model shows the full preview. The calendar messages were re-laid out with short lines and human dates, so an ordinary booking fits. A booking with a warning never fits.

| File | What it shows |
|---|---|
| `pane-5-warned-no-dialog-fallback.txt` | A warned booking (BYDAY=MO on a Sunday start) through the same dialog-capable client: **no dialog**. The preview comes back with the full five-line message, the warning, and `confirm_required`. Nothing booked. |
| `pane-6-dialog-fits-whole.txt` | The ordinary booking's dialog: `Book '…'` / `When: Sun 4 Oct 2026 04:00–04:15 Europe/London` / `Invite: sameer.modha@itv.com (no emails sent)` / `Clashes: none.` All four lines are whole. |
| `pane-7-accept-result.txt` | Space (tick Proceed) → Down → Enter (Accept). Booked as event `osk194gk4dqhg5vnpr5ir8mqk0`; `confirm_gate: "elicitation: the client answered proceed=true; booked on that answer"`. |

## Third session: the essayeur repairs (4b96683), seen live without a write

Two cold essayeurs then found that update_event could write after a decline, when the event changed between the resolver's read and the body's, and that the too-long fallback gave no reason. The repair made the gate three composed resolvers. The body now receives the words the dialog carried as well as the answer, and it writes only if the state it re-read still renders to those words. Tonight's one calendar write was already spent, so this session re-rendered the no-write paths only.

| File | What it shows |
|---|---|
| `pane-8-repaired-dialog.txt` | The same four-line dialog, raised through the composed resolvers in real CC. |
| `pane-9-repaired-decline-result.txt` | Decline: nothing booked, the decline cue. |
| `pane-10-repaired-too-long-cue.txt` | A warned booking: no dialog, and the preview's `confirm_gate` now reads `no dialog: this preview is longer than a Claude Code confirm dialog shows whole (4 lines of 74 columns on an 80-column terminal)…`. |

The accept path of the repaired code is pinned only through the in-memory mcp Client, in both protocol eras. It was not seen live, because that would have been a second calendar write.

Clean-up: the worker read the event back (summary and sole attendee matched), deleted it with `sendUpdates=none`, and re-fetched it. The re-fetch showed `status: cancelled` (Calendar still returns a deleted event by id), and the event no longer appears in a calendar listing of the window.

## Things worth knowing

- **Bypass-permissions mode does not answer elicitation dialogs.** Each dialog stayed up until the driver pressed a key (pane-3's sat for minutes). The driven model twice guessed the opposite, that bypass mode auto-accepts. It cannot see the dialog, so from inside the model this is a guess, and tonight's captures refute it.
- **The driven model twice called the decline cue's wording unverifiable.** The cue says "The user said no through the dialog", and a server cannot know a person said it. The model was wrong about who answered tonight (the driver did), but right that the claim is one the server can't make. The same wording is share's, seen live on 14 Sep and pinned verbatim, so it is filed rather than changed here (mise-jojewi).
- **The honesty rule held, unprompted.** After each call the driven model said no dialog had reached it and that only the screen could say who answered. After the accept, it flagged "whether an auto-accepted elicitation should count as the user's yes". That is policy A's recorded trade-off (mise-wagina, 2026-09-06), not a new finding.
- **Other clients are unmeasured.** The fit budget is Claude Code's. Applying it to every client errs toward the confirm= path, which is safe. A client that renders long messages in full loses nothing but the dialog on long previews.
- **Fullscreen CC may show fewer than 4 lines on a short terminal** (the bundle takes `min(4, height-derived)` there). This is unmeasured and the fit check does not model it.

Re-run: `hublot.sh start X --cwd <trusted dir without .mcp.json> -- claudev --mcp-config <this dir>/mcp-config.json`, then the prompts in the panes. `wait X 'Accept.*Decline'` catches the dialog. Answer with `key X Space` / `Down` / `Right` / `Escape`, then `enter`. Don't press Ctrl-O while a dialog is up: after it, the dialog stopped taking focus keys and only Esc worked.
