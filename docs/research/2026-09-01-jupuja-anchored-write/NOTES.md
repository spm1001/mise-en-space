# Anchored-comment WRITE probe — 2026-09-01 (mise-jupuja)

The read side (mise-dukacu) shipped the night before. This probe answers what the mise-picihi probe left unmeasured and this build must not guess: where `assigneeEmailAddress` rides, whether it is honoured, whether `writeControl` genuinely guards a stale read, and — the expensive one — **which index space `insertComment.range` is interpreted in on a document that has open suggestions**.

**Rig:** `uv run` scripts through mise's own `MiseSyncClient` (so the auth path is the shipped one), against Sameer's scratch probe artefacts from picihi, which that handoff left live and disposable. Every exchange is a numbered `NN-*.json` beside this note. **Nothing existing was modified — the probe only adds comment threads.** Eleven `[agent] jupuja probe:` threads were left behind (5 on the doc, 4 on the deck, 2 on the sheet); they are litter on a scratch file, listed at the bottom so they can be swept with the folder.

## Headline: the range is read in the SUGGESTIONS_INLINE index space (21–24)

The probe doc carries open suggestions, so the two view modes disagree about where everything is. One text run — `SUGGESTED-B: this sentence arrived…` — sits at `[98,183)` under `SUGGESTIONS_INLINE` and at `[1,86)` under `PREVIEW_WITHOUT_SUGGESTIONS`. Two comments were inserted, one aimed with each set of indices, and the response's `plainTextQuote` was read back as the oracle for what the comment actually landed on:

| Aimed with | Landed on | Intended |
|---|---|---|
| `SUGGESTIONS_INLINE` indices `[98,118)` | `SUGGESTED-B: this se` | ✅ |
| `PREVIEW_WITHOUT_SUGGESTIONS` indices `[1,21)` | `SUGGESTED-C: left OP` | ❌ **different sentence** |

**So any quote→range resolution MUST read the document with `suggestionsViewMode=SUGGESTIONS_INLINE`.** Resolve against the clean view and the comment lands on unrelated text, with a 200 and no sign of trouble — the precise failure the card's `--badly` names ("a comment landing in the wrong place reads as authored intent"). The gap is the length of every open suggested insertion above the anchor, so it is zero on a doc with no suggestions and grows silently as suggestions accumulate.

Related and non-obvious: **the Docs comments read refuses `PREVIEW_WITHOUT_SUGGESTIONS` outright** — `"Comments may not be requested when previewing suggestions."` (11). picihi recorded that comments need `suggestionsViewMode` set explicitly; this narrows it to *one legal value* when comments are also requested.

## assigneeEmailAddress — accepted inside insertComment, honoured, and unvalidated

- **Where it rides:** inside the `insertComment` request object, beside `content` (02, 06, 07). All three surfaces returned 200.
- **It sticks:** the read-back shows `headPost.assigneeEmail` on the assigned threads (12, 25). Measured on Slides and Docs.
- **Google does not check that the assignee can see the file (19, 25).** `definitely-not-a-real-person-9c1f@example.com` was accepted and stored as the assignee. There is no error, no warning and no notification path — the thread is simply assigned to somebody who will never see it. mise has to say so, because the caller cannot tell from a 200.
- Assignment to *another real person* is unmeasured — it needs a second consenting account. Parked honestly rather than claimed.

## writeControl: honoured on Docs and Slides, ignored on Sheets

- **Docs** (03, 04, 14, 15): `writeControl.requiredRevisionId` refuses a non-matching revision — `"The required revision ID '…' does not match the latest revision."` The guard is *tight*: probe 04 pinned a revision read seconds earlier and was refused, because the probe's own comment insert at step 02 had already bumped it. **A comment insert bumps the document revision.** Written back-to-back with its read (14→15), the pin succeeds — the positive control that makes the refusals meaningful.
- **Slides** (16, 17): accepts `writeControl` and succeeds when pinned to the current revision.
- **Sheets** (18): accepted `writeControl: {requiredRevisionId: "x"}` — an id that matches nothing — and returned **200**. The field is not honoured and not rejected, so there is no revision guard available on the Sheets plane at all. Do not write code that appears to rely on one.

## Anchor validation is Google's, and it is strict (08, 09, 10)

Every out-of-bounds anchor is refused with a specific message rather than clamped or silently relocated:

- unknown slide objectId → `The object (NO_SUCH_OBJECT) could not be found.`
- row past the grid → `GridCoordinate.rowIndex[99998] is after last row in grid[999]`
- range past the document → `Index 90000 must be less than the end index of the referenced segment, 352.`

Useful, but not a substitute for resolving locally: these fire on *structurally* impossible anchors, never on an anchor that is valid and points at the wrong thing.

## Batches are atomic (20)

A two-request batch with one invalid range returned 400 and landed **neither** comment — confirmed by count, five `jupuja` threads on the doc against five successful single inserts. So a multi-request comment batch cannot half-apply. (picihi's warning about `commentUpdateState: ALL_FAILED_UNKNOWN_REASON` coexisting with committed model changes concerns batches that MIX comment and content requests; mise sends comment requests alone, which keeps that case out of reach.)

## Consequences for the build

1. Resolve Docs quotes against `SUGGESTIONS_INLINE`, always. Nothing else is safe.
2. Pin `requiredRevisionId` on Docs and Slides; on a revision mismatch, re-resolve the caller's *quote* (their intent is the text, not the index) and retry, then refuse. Sheets cannot be pinned — disclose it.
3. Warn whenever an assignee is set: Google will accept an address with no access to the file.
4. Send exactly one `insertComment` per batch, never mixed with content requests.

## Threads left on the scratch artefacts

Doc `1eS6SMV2kK…`: `AAACGWvxVdo`, `AAACGWvxVdw`, `AAACGWvxVd4`, `AAACGWvxVeA`, `AAACGWvxVeI`. Deck `1wkQSlFk0e…`: the `[agent] jupuja probe:` threads from 06 and 17. Sheet `1HLogE6ENz…`: from 07 and 18. All prefixed `[agent] jupuja probe:`. Deleting a comment is irreversible, so they were left in place rather than swept — the whole scratch folder is Sameer's to trash whenever he likes.
