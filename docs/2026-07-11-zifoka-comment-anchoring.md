# Comment anchoring in `comments.md` — the confirmed picture (mise-zifoka)

**Date:** 2026-07-11 · **Outcome:** mise-newosi (Google-Doc triage channel) · **Action:** mise-zifoka

The question: when mise fetches a Google Doc that carries anchored margin comments, does the
deposited `comments.md` tie each comment to **(a)** the text it is anchored on and **(b)** the
item/section it falls in? Verified against a real, human-commented doc, not a fixture.

## Test artefact

"Bon estate — open desired outcomes (2026-07-08)" (`1TlAJ467Adc8gAiEZPT8CCLhgGDto5SwfQ8peqjhtyHU`)
— Sameer left **10 anchored margin comments** on it (real triage: "close all these", "lose all
these", "remind me?", …). The doc is a 5-section H2 hierarchy (Engine room / Product bets / ITV
work / Work-life / Parked), each section a set of bold sub-group labels over `- [ ]`/`- [x]`
outcome lines carrying inline bon IDs like `(opx-gidite)`.

## The rendered shape

`extractors/comments.py::extract_comments_content` → `_format_comment` emits, per comment:

```
### [Author <email>] • YYYY-MM-DD · `commentId`
*[RESOLVED]*                    (only if resolved)
*Mentions: @a@x.com*            (only if @mentions)

> quoted anchor text            (only if quotedFileContent present; truncated at 200 chars)

comment content

**Replies:**
- **[Author]** (date): reply text
```

Comments are a **flat list separated by `---`, in Drive-API order** (chronological-ish — newest
first), **not document order**. Anchor text comes from `quotedFileContent.value`
(`adapters/drive.py:838`), captured in `COMMENT_FIELDS`. The API's `anchor` field (an opaque,
revision-scoped region descriptor) is **not** fetched.

## (a) Anchored text — YES ✅

Every one of the 10 comments rendered its `> quoted text` blockquote. The anchor is legible and
correct. Two warts:

1. **Truncated at 200 chars** (`comments.py:155`). Comment #1's anchor spans three outcome lines
   (opx-gidite / opx-kihizu / opx-rubivu) and is cut mid-third-line — so "close **all** these"
   loses opx-rubivu from view. Multi-line-span anchors lose their tail.
2. **HTML entities not decoded.** `OP&#39;s`, `Plongeur&#39;s` render raw — `quotedFileContent.value`
   is HTML-escaped and mise never unescapes it. Minor legibility nit.

## (b) Nearest item/section — NO ❌ (the gap)

`comments.md` gives the anchored snippet and **nothing about where it sits**:

- **No section/heading context.** You cannot tell from `comments.md` that comment `…3e3HU`
  (cin-hiboza) is under `## Parked or dormant → claude-intents`, or that `…3e3HM` (plg-pazahi) is
  under `## ITV work → mit-plongeur`. The section it falls in is invisible.
- **No position / neighbourhood.** Order is API/chronological, not document order — the anchor
  line numbers run 146, 151, 193, 191, 183, 115, 64, 55, 44, 39. So you can't even infer a
  comment's neighbours from reading order. To locate a comment you must hand-grep `content.md` for
  its (truncated, HTML-escaped) anchor snippet.

**Why this bites the triage use-case specifically.** Three of the ten comments anchor a *container*,
not a leaf:

| Comment | Anchored on | What the human meant | What `comments.md` shows |
|---|---|---|---|
| `…3e3Hc` "lose all these" | the **H2 heading** "Work-life (notes board)…" | drop all 26 items in that section | just the heading text — the 26 items are invisible |
| `…3e3HY` "remind me?" | a **bold sub-group** "day (orphaned prefix…)" | the 3 `day-*` items below it | just the label — the 3 items are invisible |
| `…3e3Hg` "close all these" | a **3-line span** (opx-gidite/kihizu/rubivu) | all three outcomes | 2.x lines, third truncated |

When a comment anchors a heading or spans multiple items, the rich triage signal ("close this whole
section", "these three") requires knowing the document structure the anchor sits *in* — which is
exactly what mise does not surface. A Claude reading `comments.md` alone would under-act.

**Mitigant that is content-specific, not mise's doing:** in *this* doc the anchor usually embeds the
bon ID `(opx-gidite)`, so a Claude *can* correlate comment→item by matching the ID against
`content.md`. That is a property of Sameer's outcome lines, not of mise — a strategy doc or a prose
draft gives only the snippet, and (b) fails completely.

## Verdict for newosi

The read half is **half-legible**: the *what-was-said* + *what-text* axis is solid; the
*where-in-the-document* axis is absent. For a triage channel where Claude "reads what the human
decided", (b) is the load-bearing missing piece — a decision anchored on a heading is a decision
about everything under it, and mise currently drops that structure.

## Design implications for the fix

- The Drive `anchor` field would **not** solve (b) on its own — it is a revision-scoped region blob,
  not a section name. Deriving "nearest item/section" means **correlating the anchor against the
  fetched document structure** (`content.md` headings / list items), which mise already has in hand
  at fetch time. This is a read-side enrichment, not a new API call.
- Cheap wins available immediately: HTML-unescape the anchor, and reconsider the 200-char truncation
  for multi-line spans (or note the span count).
- The natural build is a new action under newosi: *comments.md locates each comment in the document
  tree — nearest heading + nearest list item, in document order*. This is the read-side twin of
  mise-wetera (anchored **write**); doing the read-side location first also de-risks wetera, because
  both need a shared notion of "anchor ↔ document region".
