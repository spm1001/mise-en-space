# Anchored comment *write* on Google Docs is not achievable via the Drive API (mise-wetera)

**Date:** 2026-07-11 · **Outcome:** mise-newosi · **Action:** mise-wetera (spike, not built)

> **Resolution (2026-07-11): wetera closed as WRONG GOAL, not just infeasible.** The triage
> channel's write-back need is to **reply to the human's comment threads** (`comment_reply`, already
> shipped) — a reply inherits the human's editor-minted `kix.*` anchor for free and can resolve the
> thread, so the round-trip is complete without arbitrary-anchor create. Creating new anchored
> comments (what this spike disproved) was never the need. The feasibility finding below stands as
> the record of *why* the API path is a dead-end — don't re-attempt it.

**Verdict: do not build wetera as specified.** The Drive API accepts and stores a comment
`anchor` string but does **not** resolve it into a genuine text anchor on a Google Doc. A comment
created via the API is functionally unanchored regardless of the anchor payload. Real text anchors
are minted only by the Google Docs *editor* when a human highlights text. Confirmed empirically
against real data before any implementation — the clean write-side "gold standard" the brief
envisioned is a Google platform limitation, not a mise gap.

## What the brief assumed vs. what's real

The brief guessed the anchor was a computable offset region: `{r: revisionId, a: [{txt: {o, l}}]}`.
It isn't. The real anchor on a human-created comment is an **opaque `kix.*` ID**
(`kix.gkoskz3nlj59`) — the Google Docs editor's internal named-range identifier for the highlighted
range. You cannot compute it from a text offset.

## The evidence (spike, throwaway doc, now deleted)

The read side: 10 real anchored comments on the bon-estate doc all carry `anchor='kix.<id>'` **and**
a populated `quotedFileContent` (the API's echo of the anchored text — this is what mise's `comments.md`
renders and jimive locates).

Five write attempts via `comments.create`, each read back fresh:

| Anchor payload sent | `anchor` stored | `quotedFileContent` |
|---|---|---|
| `{"r":"head","a":[{"txt":{"o":76,"l":26}}]}` (brief's offset format) | verbatim | **empty** |
| `{"region":{"kind":"drive#commentRegion","line":3,"rev":"head"}}` (Google guide's format) | verbatim | **empty** |
| `{"region":{...,"line":4,...}}` | verbatim | **empty** |
| `kix.<id>` of a Docs-API-created named range over the target text | `kix.<id>` | **empty** |
| `{"namedRangeId":"kix.<id>"}` | verbatim | **empty** |
| *(control)* a real human comment | `kix.<id>` | **"Quick wins shipped…"** |

Key sub-findings:
- **The API stores whatever anchor string you send, verbatim** — no validation, no resolution. A
  "successful" create (200 + comment id) proves nothing about anchoring.
- **`createNamedRange` (Docs API) returns a `kix.*` ID in the exact format of a real anchor**, and
  `comments.create` accepts it — so the anchor *looks* identical to a human one. But
  `quotedFileContent` still never populates, so the comment↔text linkage the editor establishes is
  **not** replicated by (named range) + (comment referencing it). The editor does something more than
  create a matching named range.
- `quotedFileContent` is the API's own canonical "what text is this anchored to." Its absence means
  mise (and any consumer reading it, incl. jimive) would treat the comment as unanchored — so even if
  a highlight rendered in the UI, the API surface mise depends on wouldn't reflect it.

## Residual uncertainty (named honestly)

This verdict rests on the API surface (`quotedFileContent`), not a visual check of the Docs comment
pane — I couldn't confirm the appliance Chrome is logged into the owning ITV account. It is
*conceivable* the named-range-anchored comment highlights something in the editor UI while
`quotedFileContent` stays empty for API-created comments. But (a) the offset/region formats — the only
ones that could target a sentence-level range — clearly did nothing, and (b) wetera's value to the
triage channel flows through the API's `quotedFileContent`, which is empty in every case. So even the
best-case UI outcome doesn't meet wetera's goal. This is a known long-standing Google limitation.

## Options for the outcome (mise-newosi)

1. **Drop/close wetera** — the clean API path is a platform dead-end. The write side of the triage
   channel maxes out at cudire (unanchored `do(comment)`), whose *content* can quote the target text
   as a textual pointer ("re: 'this row is off by 2' — …"). Lower fidelity, works today, no new op.
2. **Browser-drive real anchoring via passe** — automate the Docs editor (highlight text → comment)
   to mint a genuine `kix.*` anchor. The only route to editor-grade anchoring, but a heavy pivot:
   browser automation not API, needs the appliance logged into the owning account, brittle to UI
   change. A different capability class from mise's API surface.
3. **Park wetera as Someday/blocked** with this finding, revisit if Google ships real anchor-write
   support (or if the browser path becomes worth it).
