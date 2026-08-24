# Can Docs batchUpdate CREATE a tab? — YES (probed 2026-08-24, mise-givige)

Spike evidence for **mise-wisuzu** ("mise can place content in a new Google Doc tab"). The wisuzu brief asserted "The Docs API supports tabs" — true for read, unproven for creation until this probe. A fresh-context refuter attacked the verdict the same day; its catches are folded in below and marked.

## Verdict

**Docs batchUpdate CAN create tabs.** `addDocumentTab` is in the public Request union and works live: it mints a tab, returns the new `tabId`, and `insertText` addressed by `location.tabId` places content in it while the original tab keeps its text with no redraft bleed. Two hard constraints, both probed:

1. **The server mints the tabId — supplying your own is refused categorically** (400 INVALID_ARGUMENT, *"Tab ID should not be specified in the properties when adding a tab"*, identical for `t.`-prefixed and bare formats).
2. **The one-batch shortcut is a trap, not a route** (refuter's live falsifier): `[addDocumentTab{index:0}, insertText with no tabId]` in a single batch returns **200** — and silently writes the text into the **original** tab (Location's "first tab" default does not resolve to the just-added tab even when it sits at index 0). That is exactly the redraft-bleeds-into-live-draft failure wisuzu exists to prevent, wearing a success code.

So the route is **two sequential batchUpdates**: add → read minted id from `replies[0].addDocumentTab.tabProperties.tabId` → insert by `location.tabId`. Still one `do()` call at mise's surface.

## Evidence chain

1. **Discovery document** (fetched live 2026-08-24, and independently refetched by the refuter — identical): `https://docs.googleapis.com/$discovery/rest?version=v1`, `docs:v1` revision **20260817**. Request union = 40 types, including `addDocumentTab`, `deleteTab`, `updateDocumentTabProperties`. `AddDocumentTabRequest.tabProperties` accepts `title` / `index` / `parentTabId` / `iconEmoji`, all optional; `AddDocumentTabResponse` returns the minted `tabProperties`. (`addDocumentTab` post-dates training data — the live fetch was load-bearing, not ritual.)
2. **GA, not preview** (refuter check): the public reference page documents `AddDocumentTabRequest` with no Developer Preview marker; all 35 preview markers on that page attach to the comments/suggestions request family, which is *also absent from public discovery* — so presence in the public discovery union genuinely discriminates GA here. The live 200 came through a plain Internal OAuth client with no preview enrolment.
3. **Live probe** (`probe_add_tab.py`, run as sameer.modha@itv.com via mise's normal credential resolution, scratch doc `1jrFvxlRIvd2BanjVtYNguRBRqehIck_kZaszK66yxWg`, trashed after):
   - A: `addDocumentTab {title: 'Redraft probe'}` → 200, minted `tabId='t.nak98v8569pr'`, index 1.
   - B: `insertText {location: {tabId, index: 1}}` → 200 (the sync client raises on 4xx/5xx — `adapters/http_client.py` — so a printed OK is a real 200).
   - C: read-back `documents.get?includeTabsContent=true` → 2 tabs; original (`t.0`) **contains its original text with no redraft bleed** (containment + no-bleed assertions — *not* byte equality: the read-back carries Docs' document-final newline, so the first write-up's "byte-identical" over-stated what was asserted); new tab titled and holding the redraft text. Six checks, all PASS.
   - D: caller-supplied `tabId` → 400. Format ambiguity settled by `probe_supplied_tabid.py` (second scratch doc, response body captured, both id formats — output quoted in its docstring). D doubles as the loud-rejection control: A's 200 was not a permissive-accept.
4. **Second-subject replication + one-batch falsifier** (refuter, `probe_one_batch_fill.py`, scratch doc `1l-pd_dtnxC3cR2r5J2YWvdCc8sYn2YpZ6z2sDqts-lc`, trashed after): fresh doc, same mechanism reproduced — and the single-batch route accepted-but-wrong-tab finding in the Verdict above.

## Falsifier adjudication

The outcome's pre-registered `--badly` (Sameer): *"if we miss some clever and simple way it could happen across the bonkers API surface that is Google workspace."* It aims at a negative verdict — concluding "cannot" from too narrow a surface. The verdict is **positive on the primary surface**, proven live on two docs by two independent sessions, so the surviving-routes sweep doesn't bind. The inverted residue — a *simpler* route than two batchUpdates — was attacked directly: the only candidate (single-batch add-and-fill) was probed and found to fail silently-wrong, and supplied ids are categorically refused. No simpler route stands.

## Files

| File | What it holds |
|---|---|
| `probe_add_tab.py` | The A/B/C/D probe, self-cleaning; exit code now fails on any behavioural FAIL (refuter catch — the first cut returned 0 regardless) |
| `probe_supplied_tabid.py` | D2: supplied-tabId 400 is categorical, not a format complaint (both formats, body captured in docstring) |
| `probe_one_batch_fill.py` | Refuter's falsifier: one-batch add-and-fill → 200 but text lands in the ORIGINAL tab |
| `probe_drive_import_vs_tabs.py` | Build-phase falsifier sweep (mise-wisuzu): the Drive markdown import engine aimed at a doc with a fresh index-0 tab **flattens the doc to ONE tab** — new tab destroyed, original content destroyed, surviving tab keeps the ORIGINAL's id (`t.0`), all under a 200. Kills the full-fidelity-into-a-tab route AND measures the mise-vuloju watch item: un-guarded `do(overwrite)` on a multi-tab Doc was silent multi-tab destruction (now refused, `tools/overwrite.py`). Bonus negative: markdown H1s import as HEADING_1 paragraphs, never as tabs — no import-side tab syntax exists. |

## Open edges for wisuzu (named, not probed)

- **Markdown into a tab**: mise's rich-markdown rendering rides the **Drive import engine** (`files().update`, whole-document), while `insertText` into a tab is plain text. So "place a *markdown* redraft into its own tab" needs a design choice: native Docs API requests per construct, or plain-text-in-tab as v1. Build-item territory.
- **Watch-grade residual (refuter)**: no dated release note found for `addDocumentTab` (release-notes URL 404s), and the live proof covers itv.com only. Public-reference GA status makes a domain lag unlikely; the first mise-home (planetmodha) use is the free re-check.
