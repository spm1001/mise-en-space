# Handoff — 2026-09-22

session_id: 54a0745b-b1b6-4c9f-ae34-4e578ccff80d (targeting-responses lane, atelier, launched in ITV/mit-kg)
purpose: Field report only — mise cannot insert or fill a table in an existing Google Doc; the Docs API can, measured live. Board unreachable from the atelier (Dolt on tube), so the item is a Candidate below.

## Done
- Wrote `docs/2026-09-22-tables-in-google-docs-field-report.md` with the measured Docs API recipe and the proposed `do()` ops.

## Gotchas
- The atelier cannot reach the bon Dolt server on tube, so nothing was minted; the Candidate below needs a session with a writer.

## Next
- Mint the candidate; decide whether `insert_table` / `update_table` are one op or two; mind the 2048-char `DO_DESCRIPTION_FULL` budget (CLAUDE.md step 6) before advertising it.

### Candidates
- NEW (action, standalone): title "Field Report: tables into an existing Google Doc — insert_table / update_table for do()"; why "Four times in two days (21–22 Sep 2026, targeting-responses lane on the atelier) a live, commented Doc needed a table added or a placeholder table filled, and mise had no move: replace_text/append are plain text, overwrite kills comment anchors and existing tables. The session hand-rolled the Docs API route and Sameer ruled it is mise's job. Field report: docs/2026-09-22-tables-in-google-docs-field-report.md"; what "1. insert_table: markdown table after an anchor paragraph (or at end), cells filled, header bold, restore_point cue 2. update_table: fill an existing same-shape table found by its first cell's opening words; teaching error on shape mismatch 3. Advertise within the description budget; unit tests for both; decide remote-mode inclusion"; done "Both ops run against a real commented Doc without disturbing its comments, and a fetch reads the table back as markdown with the right values"; (candidate from the atelier session 54a0745b, 2026-09-22).
