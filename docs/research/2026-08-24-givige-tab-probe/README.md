# Can Docs batchUpdate CREATE a tab? — YES (probed 2026-08-24, mise-givige)

Spike evidence for **mise-wisuzu** ("mise can place content in a new Google Doc tab"). The wisuzu brief asserted "The Docs API supports tabs" — true for read, unproven for creation until this probe.

## Verdict

**Docs batchUpdate CAN create tabs.** `addDocumentTab` is in the public Request union and works live: it mints a tab, returns the new `tabId`, and `insertText` addressed by `location.tabId` places content in it while leaving the original tab byte-identical. One hard constraint: **the server mints the tabId — supplying your own is refused** (400 INVALID_ARGUMENT, *"Tab ID should not be specified in the properties when adding a tab"*), so add-and-fill cannot ride a single batchUpdate. The route is two sequential batchUpdates (add → read minted id from `replies[0].addDocumentTab.tabProperties.tabId` → insert by `location.tabId`) — still one `do()` call at mise's surface.

## Evidence chain

1. **Discovery document** (fetched live 2026-08-24): `https://docs.googleapis.com/$discovery/rest?version=v1`, `docs:v1` revision **20260817**. Request union = 40 types, including `addDocumentTab`, `deleteTab`, `updateDocumentTabProperties`. `AddDocumentTabRequest.tabProperties` accepts `title` / `index` / `parentTabId` / `iconEmoji`, all optional; `AddDocumentTabResponse` returns the minted `tabProperties`. (`addDocumentTab` post-dates training data — the live fetch was load-bearing, not ritual.)
2. **Live probe** (`probe_add_tab.py`, run as sameer.modha@itv.com via mise's normal credential resolution, scratch doc `1jrFvxlRIvd2BanjVtYNguRBRqehIck_kZaszK66yxWg`, trashed after):
   - A: `addDocumentTab {title: 'Redraft probe'}` → 200, minted `tabId='t.nak98v8569pr'`, index 1.
   - B: `insertText {location: {tabId, index: 1}}` → 200.
   - C: read-back `documents.get?includeTabsContent=true` → 2 tabs; original (`t.0`) text byte-identical, no redraft bleed; new tab titled and holding the redraft text. Six checks, all PASS.
   - D: caller-supplied `tabId` → 400 with the message quoted above, for both `t.`-prefixed and bare formats (second run, fresh scratch doc, body captured). D doubles as the loud-rejection control: A's 200 was not a permissive-accept.

## Files

| File | What it holds |
|---|---|
| `probe_add_tab.py` | The A/B/C/D probe, self-cleaning (trashes its scratch doc in `finally`) |

## Open design edge for wisuzu (named, not probed)

mise's rich-markdown rendering rides the **Drive import engine** (`files().update` with `text/markdown`), which targets the whole document, not a tab. `insertText` into a tab is plain text. So "place a *markdown* redraft into its own tab" needs a design decision: native Docs API requests per-construct, or accept plain-text-in-tab as v1. That is wisuzu build-item territory, out of this spike's scope.
