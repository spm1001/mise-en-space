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

## Likely build items (held until the eyeball answers land)

- `comments.md` slide/cell locators for Slides/Sheets fetches (preview read or legible Drive anchors, whichever proves true for UI-authored comments).
- Anchored `do(comment)` — anchor param (slide id / A1 / text quote), assignee option.
- mise-mikawi fix: route Doc comment creation through Docs-API `insertComment` (visibility pending check #1).
- Resolve/reopen already exists in `do(comment_reply)` via the Drive plane — consider whether the batchUpdate plane buys anything extra there (probably not; don't churn).
