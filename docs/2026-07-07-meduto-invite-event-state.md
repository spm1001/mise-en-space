# meduto exploration — invitation emails carry no live Calendar state

**Date:** 2026-07-07 · **Bon:** mise-meduto · **Probes run live against the repro thread** (`19ef4ccdff5042ab`, the cancelled Sameer/Ellie 29-Jun invite that mise showed as a live unread invite while Gmail's UI showed "Event Cancelled").

## The mechanism (Sameer's "pas-de-deux")

An invitation email is a **frozen snapshot**: its `text/calendar` (ICS) part says `METHOD:REQUEST`, `STATUS:CONFIRMED` forever — verified on the repro thread, whose email still says CONFIRMED three weeks after cancellation. Gmail's *UI* overlays live Google Calendar state (cancelled / RSVP'd / rescheduled) on top of the email at render time. Mise reads only the email, so it repeats the snapshot as if current.

## Q1 — Can mise resolve an invite to its live event? YES, one join + one call

1. The invite's ICS part carries the join key: `UID:2ma8jaquv7dsb83auonik8oi7t@google.com`. (On the repro, the ICS body was **not inline** — it rode as an attachment, so extracting the UID cost one `attachments.get`.)
2. `GET calendars/primary/events?iCalUID=<uid>&showDeleted=true` returns the live event:

```json
{"status": "cancelled",
 "start": {"dateTime": "2026-06-29T15:00:00+01:00"},
 "updated": "2026-06-26T15:59:41Z",        // when it was cancelled
 "my_response": "needsAction"}             // attendees[self].responseStatus
```

**Trap (load-bearing):** with the default `showDeleted=false` the same lookup returns **0 items** — a cancelled event is *invisible*, not *marked cancelled*. A naive probe concludes "no such event". Any implementation MUST pass `showDeleted=true`.

Status / my responseStatus / current start time / cancellation timestamp are all in the one response. Rescheduling is covered free: `start` is always the *current* time.

## Q2 — Should mise enrich? Recommendation: yes, fetch-side; cue-only search-side

Split by cost, matching the triage flow (search flags → fetch confirms):

- **Search:** add a free `has_invite` boolean per gmail result — the search fields mask already fetches every part's `mimeType` (post-samono), so detecting a `text/calendar` part costs nothing. **No live lookup at search time** (would be +2 calls × N threads).
- **Fetch (gmail thread with an ICS part):** +2 API calls (attachment fetch for the UID, `events.list` by iCalUID) → add cues:
  `invite_state: {status, my_response, current_start, cancelled_at?}` and a **warning** when `status == "cancelled"`: the email body is a stale snapshot. This is the fix for the repro-class failure (facteur flagging a cancelled meeting as "happening now").
- Guest mode: a guest token may carry no calendar scope (kivane pattern) — skip enrichment silently, never fail the fetch.

Filed as a build action: **mise-?** (see board — "Fetched invites disclose live Calendar state").

## Q3 — Is the 'Cancelled:' notice email enough? No

The notice is a *separate* message with `METHOD:CANCEL` — window-dependent (a search may surface the invite without the notice), thread-placement-dependent, and itself frozen (silent about later re-instatement or re-scheduling). Direct event-state read is authoritative *now*-state for one call. Use the notice only as a hint that state changed; never as the state.

## Q4 — Does this open RSVP-write? Structurally yes, gated by scope

Same surface: `events.patch` on `attendees[self].responseStatus`. But mise holds **`calendar.readonly`** (oauth_config.py:40) — write needs `calendar.events`, which is a scope upgrade → re-consent for every existing user. Deliberately out of scope until a real need lands (facteur rules 2/8 would be the consumer); the read-side enrichment above needs **no scope change** — verified live on the current token.
