# What Google Docs does to control characters on the way in (probed 2026-08-24, mise-melaso)

Field-report evidence for **mise-melaso**. The card arrived with one path already measured by the mise-wisuzu essayeur — Docs `batchUpdate` `insertText` deletes `\f` and `\x00` under an HTTP 200 — and one question open: **does the Drive markdown import preserve them?** It does not, and the answer for `\x00` is worse than the card assumed.

## Verdict

| character | Drive markdown import (`create`, `overwrite`) | `insertText` / `replaceAllText` (`prepend`, `append`, `append tab=`, `replace_text`) | plain byte upload (`doc_type='file'`) |
|---|---|---|---|
| `\f` form feed | **deleted** | **deleted** | survives |
| `\x00` NUL | **TRUNCATES the document there** | deleted | survives |
| `\r\n` | joined to a space (markdown soft wrap) | normalised to `\n` | survives |
| `\t` | survives | survives | survives |
| `\v` | survives | survives | survives |

Two findings worth the item:

1. **`\f` is mise's own page marker.** `pdftotext -layout` separates PDF pages with a form feed; `pdf_page_fidelity` (tools/fetch/common.py) counts them into the deposit's `page_markers`; `extractors/pdf_anchors.py` places exhibit anchors on them. So the commonest paste in the product — a PDF deposit's `content.md` into a Doc — lost every page boundary in silence, while the fetch cue went on promising per-page citations. An isolated form feed on its own line is deleted too (probe A5): the import has no page-break reading of it.

2. **`\x00` costs the whole tail on the import path, not just the byte.** `HEADMARKER / BEFORE\x00AFTER / TAILMARKER` imported as a document ending at `BEFORE` — HTTP 200, no cue, no error (probe A3). On `do(overwrite)` that is destruction rather than omission: the old content has already been replaced, and only the head of the new content arrives.

## Remedy shipped

`tools/doc_control_chars.py` converts each form feed to a `---` line and strips NULs before either engine sees them, and cues `page_breaks_marked` / `nuls_removed` plus a warning naming the transform, on every Doc write path. Converting rather than merely disclosing is what earns the done-criterion's stronger half — **a `---` line imports as a real `horizontalRule` element** (probe F1), so a page boundary survives *visibly* instead of being reported as lost. The plain-file paths are deliberately excluded: their bytes go up untouched (probe E), which makes `doc_type='file'` the lossless route when the exact bytes matter.

## Evidence chain

Every doc was created through mise's own `do(create)` under the normal credential resolution (sameer.modha@itv.com), read back through **both** `documents.get?includeTabsContent=true` and the Drive `text/plain` export, and trashed in a `finally`.

1. **`probe_control_chars.py`** — five paths, one sentinel per character (`FFA\fFFB` and friends, so a fused pair, a survived pair and a converted pair are all distinguishable). Paths A (create), B (append), C (append tab=), D (replace_text), E (plain `.md` upload). The read-back renders non-text structural elements into the text stream (`<horizontalRule>`, `<pageBreak>`, `<block:sectionBreak>`) — without that, a form feed converted into a real page break would have read as a deletion, which is the wrong verdict wearing the right shape.
2. **`probe_import_nul_truncation.py`** — the follow-up A2/A3/A4/A5 run, because "the tail is missing" is a big claim off one observation. A2 removed the NUL and the tail came back, isolating the cause from length or the CRLF; A3 located the cut exactly at the NUL; **A4 is the known-positive control** — the same document shape with a plain `X` in place of the NUL kept its `TAILMARKER`, so the absence in A3 is a property of the subject and not of the probe; A5 put a lone form feed on its own line and got no page break, no section break, nothing.
3. **`probe_marker_renders.py`** — measures the *remedy* before it is written down, since "imports as a horizontal rule" is a claim no mocked test can make. F1: PDF-shaped content through the sanitiser then `create` → two `horizontalRule` elements, no literal `---` text. F2: the same through `append` → the literal `---` arrives (insert paths are plain text, as the tab cue already says). F3: NUL-bearing content sanitised → `TAILMARKER` present, i.e. the strip is what saves the tail A3 lost.
4. **`probe_pdf_deposit_e2e.py`** — the closing proof on a real document rather than a synthetic string: fetch a 58-page Drive PDF, `do(create, source=<deposit>)`, count what Google built. **58 form feeds in `content.md` → 58 horizontal rules in the Doc, 0 form feeds, `cues.page_breaks_marked: 58`.**
5. **Unit layer** — `tests/unit/test_doc_control_chars.py` (30 cases) asserts on the bytes/JSON that would cross the wire per path, not on the transform alone. Mutation-controlled three ways before its green was trusted: with the transform stubbed to a pass-through, 15 cases went red across all five paths; with only the tab call site disabled, exactly the two tab cases went red and the append cases stayed green (so the tests discriminate paths rather than sharing one); and with the sanitise moved ABOVE the plain-file routing, the plain-file preservation case went red (so that guard rail has teeth).

## An instrument fault worth recording

The end-to-end probe's first cut read `page_markers` off the fetch result's **`cues`** and printed `None` for five consecutive PDFs — which reads exactly like "no PDF in this Drive has page markers". `page_markers` rides the deposit's **`manifest.json`**, not `cues`. One of those same five PDFs had 58. The null was a fact about where the probe looked.

## Residual edges — named, not probed

- **Sheets are unmeasured.** `do(create, doc_type='sheet')` and `do(overwrite)` on a spreadsheet go up as `text/csv` through a *different* import engine, and `sheet_overwrite` writes through the Sheets values API. Neither was measured here and neither is transformed — a guess either way would be the fault this item exists to fix. `sanitise_for_import` gates on `doc_type == "doc"` for exactly that reason.
- **A form feed inside a markdown table row** would split the row, because the marker carries blank lines. mise's own extraction cannot produce that shape (pdftotext emits `\f` only after a complete line at a page boundary), and the transform is cued every time, so the trade is a disclosed odd render against a silent loss. A form feed inside a fenced code block was pinned instead of left to chance: it degrades to one more code line inside the block (`test_form_feed_inside_a_fence_keeps_the_block_intact`).
- **Gmail draft bodies** take `content` too and were not measured. They are MIME, not Docs, so the two engines here do not apply — but nobody has checked what Gmail does with a form feed in a body.
