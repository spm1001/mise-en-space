# Handoff — 2026-08-24 (queue-dispatched fidelity pair: mise-gerefe + mise-rubucu, both CLOSED)

session_id: queue worker (loop dispatcher), same worker as the 0915 (givige) and 1010 (gogovi) handoffs
purpose: the fidelity pair — docx figures on fetch, markdown footnotes on create/overwrite — each with its own essayeur (fresh-context falsifier subagent) and verdict
format: fond-v1

## For the next Claude

### Done — card A (mise-gerefe, CLOSED): docx figures survive fetch

- **Embedded images in a fetched docx now land as `figure-N.ext` sidecar files referenced from content.md — no base64 inline, ever.** Base64 data-URIs lift via the new pure extractor `extractors/markdown_images.py` (bytes come from the ref's own definition — no ordering assumption); refs Drive's export drops are salvaged from the docx zip's media parts ONLY when drawing-order alignment is provable (refusal logic in `adapters/office.py`); everything else is named unrecoverable. The old warning ("N inline image(s) were dropped") counted `<w:drawing>` in the SOURCE and was measured 0-for-3 inverted — deleted.
- **Live proof on both field-report docs**: Ebiquity (`1PSh67…`) both charts viewable (essayeur re-rendered both against captions), content.md 43.6KB→14.5KB; Barb (`1bfDGZ…`) 3/3 figures, 153KB→12KB, tracked-changes warnings intact. Grep counts over docx deposits are now trustworthy.
- **The essayeur REFUTED the first salvage cut** — imageN→Nth-blip matching attaches WRONG bytes with a confident warning under native charts, legacy VML (`w:pict`), or `mc:AlternateContent` (three live probes; the regression tests in `tests/unit/test_office.py` and `tests/unit/test_doc_footnotes.py` are the durable form of those probes). Fixed same day: salvage refuses with the construct named whenever charts/VML/OLE/AlternateContent/drawing-count-mismatch are present (regression-tested for VML + charts). **Salvage has never fired on a real doc** — Drive's Aug-2026 export retained everything as base64 on both re-fetches; July's dangling-ref behaviour did not reproduce (export-engine drift, as the 08-08 brief correction suspected).
- **Accepted residuals, named on the brief**: header/footer images silent-absent (never in the export — unchanged from before); an image Drive drops at IMPORT with no ref minted leaves nothing to reconcile; .docm/.dotx never route here; Google-Docs-exported docx untested but tier-1-equivalent.

### Done — card B (mise-rubucu, CLOSED): markdown footnotes become real Docs footnotes

- **`[^N]` + `[^N]: definition` in `do(create)`/`do(overwrite)` doc content now creates real Docs footnotes** (superscript reference, footnote pane), round-trip proven: fetch renders them back as `[^N]` + definitions. Design: definitions strip pre-import; post-import pass (`tools/doc_footnotes.py`, the doc_chips pattern) — one batchUpdate of `createFootnote`@anchor-end + `deleteContentRange` pairs descending (writeControl pins the revision), replies map footnoteIds by order, a second batch fills segments. Routes enumerated on the bon (Drive-import, Docs-API pass CHOSEN, HTML import dismissed, docx intermediary dismissed-as-heavy, Apps Script dead end).
- **The essayeur REFUTED the first cut on silent-wrong, three ways, all fixed same day** (`4eba33c`): UTF-16-vs-code-point arithmetic in the shared `find_placeholder_indices` (emoji before an anchor ate letters with green cues — latent in chips too, fixed for both, `_utf16_len` in `tools/doc_chips.py`); fenced/inline code parsed as live footnote syntax (now masked with `convert_fenced_blocks`' own fence rules; a post-import anchor appearing twice is refused as ambiguous, never guessed); duplicate definitions silently lost the first (now literal + warned). Rode along: honest batch-2 failure message, orphan-anchor warning hole closed, GFM no-space defs, CRLF, and the READ side dropped its `---` before footnote definitions — it re-imported as a real horizontal rule, accreting one per md→Doc→md cycle.
- **Accepted shape limits, in the skill doc**: labels renumber (`[^note]` → `[^1]` — Docs footnotes are numbered); table-cell anchors unreachable (honest appended-literal degrade + cue); single-line definitions only.

### The publish story — read this before touching versions

**Suite 1.75.3 (published mid-build by the bapije close lane) carried card B's FIRST CUT — including the corruption bugs — for ~11 minutes (published 10:29, superseded 10:40).** The fleet's publish stamped everything on main at cut time, which included `c1c1c7c` before the essayeur pass had run. That inverted this session's bank-vs-publish decision: **1.75.4 was published from this session** (assemble green, installed artefact verified to carry `_utf16_len`, `_mask_code`, and the salvage refusal) specifically to supersede it. Lesson for queue workers: on a day the fleet is actively publishing, an unhardened commit on main is already shipping — either hold first cuts off main until the essayeur has run, or publish the fix yourself as this session did.

### Uncertain / watch (holder: bon mise-vuloju)

- Multi-tab docs on overwrite: the footnote locator reads the legacy first-tab view; whether Drive markdown overwrite even preserves tabs is unmeasured. Anchors outside the first tab would degrade loudly (appended-literal + cue), not corrupt. Watch-grade.
- The `# Tab 1` header + top `---` furniture in fetched docs pre-dates this work (control-armed) and still costs round-trip byte-identity; the footnote block itself round-trips clean now.

### Risks

- None open on these two cards; both essayeur-hardened, suite 2665 green, mypy at baseline, stdio smoke 10/10, installed 1.75.4 verified. Source commits all pushed; this handoff commits with the cold-read folds.

### Opportunities

- `find_placeholder_meta` (counts + revisionId) is now available to the chips pass too — chips currently use the plain wrapper; adopting writeControl there is a one-line hardening if chip-time concurrent edits ever bite.
- mise-dahune's sibling wish (Sheets `add_sheet` exposure) now has a proven wisuzu-family pattern to ride, per the 0915 handoff.

## For Claudes to come

Two lessons with legs. **A shared helper's latent fault detonates in the first consumer with a new usage shape**: `find_placeholder_indices` did code-point arithmetic against UTF-16 indices for months, harmlessly, because chips placeholders sit at paragraph start — footnotes put mid-paragraph text after emoji and the fault fired. When adopting a shared helper for a new shape, ask which of its assumptions the OLD consumers never exercised. **And on a fleet-publishing day, main IS the release channel**: the 11-minute corrupting publish happened because "commit to main, harden after" met "another lane cuts the suite from main" — the essayeur-before-main ordering is cheap insurance whenever the publish cadence is hot.
