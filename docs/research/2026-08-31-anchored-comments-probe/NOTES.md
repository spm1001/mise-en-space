# DPP anchored-comments probe — 2026-08-31 (mise-picihi)

Google announced Slides API anchored comments in Developer Preview on 2026-08-31 (DPP email, deposited at `.mise/gmail--google-workspace-developer-preview-program-google--1a058b1fa154`), joining Docs (comments + suggestions) and Sheets (comments) already in preview. This probe establishes access and the live behaviour of read + create + reply/resolve on all three surfaces, through mise's own OAuth client.

**Rig:** `probe.py` then `followup.py` (stdlib-only, `uv run --script`), token = the live mise token (`sameer.modha@itv.com`, client `mit-workspace-mcp-server`). Every HTTP exchange is in the numbered `NN-*.json` files beside this note (request minus auth header, status, response). Scratch artefacts live in one Drive folder: [probe folder](https://drive.google.com/drive/folders/1Of4FO88kXpMhTrKFn3Q2BYboaIVvMSzb) — [doc](https://docs.google.com/document/d/1eS6SMV2kKwNW_uIm5Kez6V_6UVwmt0y9U_lZqCuioAo/edit) · [deck](https://docs.google.com/presentation/d/1wkQSlFk0ey8Asa6-Z4fptwBXpDNjRWaQPCR4Jw_A5NM/edit) · [sheet](https://docs.google.com/spreadsheets/d/1HLogE6ENzSAHsFdGMI_HxpRFK__2AKZw1ngEYlPkIDg/edit). Left in place pending the UI eyeball checks below; trash the folder after.

## Headline

**Enrollment is live for our client+user as-is.** No project allowlisting step, no scope change — the preview endpoints answered first try on the existing token (scopes `documents`/`presentations`/`spreadsheets`/`drive`, minted 2026-08-19).

## Per-surface findings

| Surface | Read (`commentsViewMode`) | Create (`insertComment`) | Reply / resolve / reopen | Anchor legibility |
|---|---|---|---|---|
| **Docs** | 200 — but ONLY with `suggestionsViewMode` explicitly set (bare `commentsViewMode` is a 400: "Comments may only be explicitly included if inline suggestions are also explicitly requested"); `includeTabsContent=true` also required (evidence 06, 15) | 200, anchored to text `range` (07) | reply 200 (16); resolve/reopen proven on Sheets, same request shape | `anchorId: kix.…` (opaque) **plus `plainTextQuote`** — the anchored text travels with the thread (15) |
| **Slides** | 200, no extra params (09, 18) | 200, anchored to slide `objectId` (10) | reply 200 (17) | `commentAnchors` map anchorId → `objectAnchors[{objectId}]` — resolves EXACTLY to the slide ids mise already deposits in `slides_index` (18). Anchor shapes on create: `objectId` \| `shapeTextAnchor` \| `tableCellTextAnchor` \| `tableAnchor` |
| **Sheets** | 200, no extra params (12, 14) | 200, anchored to `GridCoordinate` (13) | reply+RESOLVE 200 (23), reply+REOPEN 200 (24) | `commentAnchors` map anchorId → real `GridRange` (B2 = rows 1–2 × cols 1–2) (14) |

All three surfaces support `assigneeEmailAddress` on create and `assigneeEmail` on reply (documented; not probed — needs a second human to avoid assigning noise).

## Cross-plane: what the Drive comments API sees (19–21)

mise's existing `comments.md` machinery reads Drive `comments.list`. For the API-created comments, Drive returns the SAME anchor strings — and for Slides/Sheets they are legible JSON (`{"pages":["p"],"type":"page"}`, `{"type":"workbook-range",…}`), not the historical opacity that forced the flat render. Scope honestly: **this is measured for API-created comments only** — whether UI-authored comments now expose anchors (on either plane) is eyeball-check #2 below. Sheets' Drive-plane anchor carries an opaque range uid; the real GridRange lives only in the preview read.

## Open checks (need human eyes / a UI-authored comment)

1. **The mikawi visibility twin.** The probe doc holds two comments: the anchored Docs-API one (with reply) and a `CONTROL:`-prefixed document-level Drive-API one (22) — the mise-mikawi shape that historically renders nowhere in the UI. One glance answers: is the anchored one visible on its text? Is the control still invisible?
2. **UI-authored anchors.** Add a comment by hand to any slide of the probe deck (and ideally a cell of the sheet). A re-read of `presentations.get?commentsViewMode=…` then answers whether human comments arrive with resolvable anchors — the load-bearing question for `comments.md` slide locators, since the whole point is locating comments humans make.
3. **Assignee lever** — unprobed, needs a consenting second account.

## Caveats for adoption

- Developer Preview: surface may change before GA; no SLA. Fine for additive cues; think before making a default render depend on it.
- The Docs read coupling (comments ⇒ explicit suggestions mode) means the comments read composes with mise's existing `suggestions=` machinery rather than being a free-standing call — first fetch is already SUGGESTIONS_INLINE, so the pairing is natural.
- Sheets/Slides preview reads needed no extra params — `commentsViewMode` alone was honoured.
- `resolved` on the Drive plane came back absent (not `false`) for open threads — the usual jq-null trap when consuming.

## Re-read after Sameer's UI replies (evidence 25–32, same evening)

Sameer replied in the UI on all three probe artefacts; the replies arrive cleanly in the preview reads (25–27) — but replies aren't new threads, so the UI-authored-anchor question was settled read-only against **live colleague-commented files** found via Drive activity (evidence 31–32, REDACTED to structure because this repo is public):

- **Sheets — confirmed.** Two pre-existing colleague threads on the Melt SoW sheet both carry workbook-range anchorIds resolving to exact GridRanges (C12, D14), each with `plainTextQuote` (the anchored cell text). UI-authored comments are fully locatable.
- **Docs — confirmed, with an honest edge.** Of two threads on the ADR 044A draft, one carries `kix.` anchorId + a 161-char `plainTextQuote`; the other has neither — a document-level or anchor-orphaned thread, rendered distinguishably rather than silently alike.
- **Slides — still pending**: needs one NEW UI thread (not a reply) on the probe deck; no commented deck surfaced in recent activity.

Also from the thread replies: Sameer's mechanism detail for mise-mikawi — last week's unanchored comments "went straight to 'resolved'", pointing at the UI's hide-resolved-by-default rather than never-rendered (though today's control reads `resolved: false` on the Drive plane, so the auto-resolve may be conditional — the eyeball on the control still discriminates). And the direction note: agent-identity commenting via a service account (`mit.kg@itv.com` flavour) + `assigneeEmailAddress` is the mise-lobuha shape these endpoints now make real.

## Build items (filed 2026-08-31)

- **mise-dukacu** — `comments.md` slide/cell locators for Slides/Sheets fetches (preview read or legible Drive anchors; Slides UI-authored confirmation pending as above).
- **mise-jupuja** — anchored `do(comment)` (slide id / A1 / doc text quote) + `assignee=`, on the batchUpdate plane; unanchored default unchanged pending the mikawi visibility answer.
- mise-mikawi annotated with the mechanism detail and the Docs-API fix route.
- Resolve/reopen stays on `do(comment_reply)`'s existing Drive plane — the batchUpdate plane is shape-identical there and buys nothing (probed 23/24); deliberate non-adoption.

## Still open on mise-picihi

1. UI eyeball on the probe doc: anchored Docs-API comment visible? `CONTROL:` Drive-plane comment visible or hidden?
2. One new UI comment thread on the probe deck, then a re-read for its anchor.
3. Assignee lever unprobed (needs a consenting second account — or the future agent identity).
