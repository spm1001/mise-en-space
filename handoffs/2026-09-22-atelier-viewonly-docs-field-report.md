---
session_id: da3765ed-29b5-4777-bd1c-6db963474e05
purpose: field report from the atelier - fetch 403s on every view-only Google Doc because it asks for SUGGESTIONS_INLINE, which needs comment access; the Docs API default view reads them fine
author: claude/fable-5-1
format: fond-v2
---

# Handoff — 2026-09-22 (field report, candidate mode)

## For the next Claude

### Done
- Nothing in this repo. Filed from the targeting lane on the atelier (mit-kg, commercials-and-finance room), where five internal ITV Docs that Sameer can open all failed through mise.

### The failure, measured
- `fetch(<view-only Doc URL>)` returned `permission_denied`: `403 ... documents/<id>?includeTabsContent=true&...&suggestionsViewMode=SUGGESTIONS_INLINE` with the API message "You do not have permission to access the document suggestions". Five of five Docs on 22 Sep 2026 (ids `1Bu1JUgTl_6VRDtQeQsGshpk7FU9nYH8h5w8tJWtPK4E`, `1hEB8k2rkR6RoMiDJK2W7DpSKiYvkDt1f_Ql3Dvu9Lh4`, `1XZPYF604-nEf4zST-ecCvN7XmdK4cHVt6PO7vyva3Lc`, `1rjFiT7mQZ9iOJ7uzWqTQNWinzkj_8nCzV94sd231uTs`, `1zLmj_s1QPXsvehR_WCslMmGNo3_b5epdFNbwql4lX7w`), all shared to Sameer as viewer.
- Control: `documents.get(documentId, includeTabsContent=True)` with no `suggestionsViewMode` (DEFAULT_FOR_CURRENT_ACCESS) under ADC returned all five in full. Script: `~/briefs/docs/doc_text.py` on the atelier.
- Cost: the previous session's handoff recorded these Docs as "the API 403s; Sameer opens them", and Sameer was asked to open five Docs he did not need to. The error text says "permission" and reads as a sharing problem, not a view-mode one.

### Candidates (folded 2026-09-22 into mise-tiroti, re-parented under mise-kuvuwe — the defect was already filed twice; no new item minted)

<!-- Board visible, writer unreachable (dolt on tube) — a writer-bearing /open mints or drops each; unminted = wish. -->
Provenance: Claude Code on the atelier, session da3765ed-29b5-4777-bd1c-6db963474e05 — 2026-09-22

- **NEW** action (Field Report) — "fetch falls back to the default suggestions view on 403 for view-only Docs"
  - why: a viewer-role Doc is the commonest sharing state for anything ITV-internal, and today every one of them is unreadable through mise while trivially readable through the raw API; the 403 message blames permissions, so sessions conclude "no access" and hand the human busywork.
  - what: on `403 ... document suggestions` from `documents.get` with `suggestionsViewMode=SUGGESTIONS_INLINE`, retry with the view mode omitted (or `PREVIEW_WITHOUT_SUGGESTIONS`), deposit as normal, and cue `suggestions_mode: "unavailable (viewer access)"` plus `has_suggestions: null` so the After-Every-Fetch checklist reads it honestly. The fetch skill's "Docs with suggested edits" section should say that `suggestions=` views need comment access.
  - done: the five ids above fetch through mise with a cue naming the fallback; a unit test pins the 403 → retry path with a known-bad control (a genuine no-access 403 must still refuse).

### Risks
- A genuine no-access 403 and this one share the status code; the discriminator is the API message ("document suggestions") and the query param. Retry only on that message, or a real permission failure gets masked by a second, identical failure.

## For Claudes to come
Read the URL in a mise 403 before believing the word "permission". The query string names the view mode, and a view-only Doc rejects the suggestions view specifically. The default view is the control that separates "I can't see this Doc" from "I can't see its suggestions".
