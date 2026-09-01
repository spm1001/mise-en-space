# Suggest-mode probe, and the Word-import corpus — 2026-09-01 (mise-hupago)

mise-picihi established that `writeControl.writeMode=SUGGEST` turns a Docs batchUpdate into tracked changes. This probe answers what that costs in practice, and — because Sameer's falsifier for this card is *"we mishandle weird comments and markup like those imported from MS Word"* — it does so against **documents Google's own `.docx` converter produced**, not native Docs.

**Rig:** two `.docx` packages built as raw OOXML (`zipfile` + hand-written `word/document.xml`), uploaded through Drive's converting import into the picihi scratch folder, then read and written through mise's own client. Building the source by hand matters: a fixture invented in the shape we expected would have measured our expectations, and Google's converter is the thing under test.

| Doc | Source | What it carries |
|---|---|---|
| `1yHr3MpzUh…` | tracked-changes .docx | `w:ins`, `w:del`, a `w:comment` anchored to a paragraph |
| `1py-Yj5sRX…` | NBSP .docx | words joined by U+00A0, plus one plain-ASCII control sentence |

## Word tracked changes become real Docs suggestions (01)

`w:del` → `suggestedDeletionIds`, `w:ins` → `suggestedInsertionIds`, and the delete/insert pair **shares one suggestion id** — which is why mise's existing markup render produces `{--twelve--}[s1]{++fourteen++}[s1]`, reading as a replace rather than two unrelated edits. The whole fold lane depends on this and nothing in the API docs promises it, so it is pinned in `tests/unit/test_suggest.py` against a fixture cut from this very import.

**Imported suggestion ids are spelled differently.** Native Docs mint `suggest.<hash>`; a converted `.docx` mints `suggestIdImport<uuid>_N`. A passthrough matching only `suggest.` refused a perfectly valid id on exactly the documents this card is about — caught by the fixture-backed test, fixed by matching the family.

## The Word comment arrives anchorless-ish (02, 04)

It survives on the Drive plane with its text intact, and in the preview read it carries `anchorId: "kix.cmt0"` with **`plainTextQuote: None`** — an anchor id minted by the converter, with no anchored text travelling beside it. Worth knowing for anything that treats a quote as proof of an anchor.

## Reading comments forbids the clean suggestions view (11, earlier picihi note)

`commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED` with `suggestionsViewMode=PREVIEW_WITHOUT_SUGGESTIONS` is refused outright: *"Comments may not be requested when previewing suggestions."* picihi recorded that comments need `suggestionsViewMode` set explicitly; this narrows it to one legal value.

## The headline: NBSP joins make a suggested replace do nothing, quietly (06)

On the NBSP import, the same paragraph took:

| `find` | occurrencesChanged | suggestions created | commentUpdateState |
|---|---|---|---|
| ordinary spaces | **0** | none | `NO_UPDATES_REQUESTED` |
| NBSP spaces | 1 | `suggest.2q4jb48am2xh` | `ALL_SAVED` |

Both **HTTP 200**. Under a direct edit, zero occurrences is a cue and the estate has long known the NBSP trap. Under `suggest=` it is materially worse: the caller has been told a change is sitting in the document awaiting a human's review, and there is nothing there at all. So mise now RAISES on a suggested replace that matches nothing, and — only on that failure path, so the read is never paid otherwise — checks whether the NBSP spelling would have matched and says so.

## An edit over existing suggested text COALESCES, and reports no new id

Found by the live run, and it reversed a guard that every unit test had passed. Replacing text that already sits inside a pending suggestion is absorbed into **that** thread: `occurrencesChanged: 1`, the document visibly changed, and `createdSuggestionIds` **empty**. The first implementation raised on an empty id list — which is the inverse error and worse than the one it guarded, because the caller is told nothing was created while a tracked change really is pending, and a retry double-applies. An empty id list on a landed edit now reports `cues.coalesced` instead.

The read-back that settled it, after a suggested `Quarterly revenue` → `Annual revenue` that mise had just reported as failed:

```
[   1] INS[suggest.2q4jb48am2xh]                  'Annual\xa0revenue'
[  15] DEL[suggest.2q4jb48am2xh]                  'Quarterly\xa0'
[  25] INS[...] DEL[...]                          'revenue'
```

The edit had landed. Only reading the document afterwards showed it — the response alone said the opposite.

## Consequences for the build

1. Zero occurrences under `suggest=` raises, with an NBSP diagnosis when that is genuinely the cause (and silence when it is not — a confidently wrong cause is worse than none).
2. An empty `createdSuggestionIds` on a landed edit means coalesced, not failed.
3. `commentUpdateState` is read on every suggest batch; it can report failure while model changes commit.
4. `[sN]` tags are ordinals for rendering, resolved fresh per call and one at a time, because folding one renumbers the rest.
5. `suggest=True` off the Docs plane refuses. Silently dropping it would make a real edit while the caller believes a proposal is waiting — the inversion this feature exists to prevent.

## Litter

Two scratch Docs created in the picihi folder (`1yHr3MpzUh…`, `1py-Yj5sRX…`), plus suggestion threads left on both by the probes. Nothing outside that folder was touched, and no pre-existing content was modified. The folder is Sameer's to trash whenever he likes.
