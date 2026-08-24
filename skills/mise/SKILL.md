---
name: mise
description: Orchestrates content fetching via the mise MCP server's search/fetch/do tools. Load before using search/fetch/do — invoke first when you see 'search Drive', 'search Gmail', 'find docs about', 'fetch this document', 'research in Workspace', 'move this file', 'create a doc', 'triage my inbox', 'archive these', 'draft an email', 'book a meeting', 'when are they free'. Covers research loops, Gmail triage with batch ops, email drafting, calendar booking and free-slot finding, and result filtering the tools alone don't know. (user)
allowed-tools: [Bash, Read, "mcp__plugin_mise_mise__*", "mcp__mise__*"]
---

# mise

Content fetching for Google Drive and Gmail — via the mise-en-space MCP.

## First Run (no token yet)

If the MCP server returns an auth error, the user needs to authenticate with Google. **Do this for them — don't ask them to type CLI commands.**

```python
mise.do(operation="setup_oauth")
```

This opens a browser at Google's consent screen on the user's Mac, runs a localhost listener, exchanges the auth code, and stashes the token in macOS Keychain. The MCP call returns immediately with the consent URL inline as a fallback (in case the browser didn't auto-open). Once the user sees "Authorization Successful" in the browser, retry the original mise call.

If `setup_oauth` itself fails (e.g. port 3000 in use), the error message will name the remediation. The CLI fallback (`uv run python -m auth --auto` from the mise-en-space repo) exists for users running mise outside Cowork/Desktop, but `setup_oauth` is the path to default to.

## Identity & multi-account

When multiple Workspace connectors are loaded in the same session — Cowork's native Drive/Calendar bound to one Google account, mise bound to another — the connector names alone don't say which is which. **Mise responses self-disclose: `cues._identity.email` shows the authenticated email on every response.** Read it, especially when the user has both a personal and a work Workspace identity active.

When in doubt about which account a question targets, prefer `mcp__plugin_mise_mise__*` (or whatever name your runtime gives mise's tools) over generic Drive/Gmail tools — mise's binding is explicit. If you've fetched data and the user reacts with "that's not the account I meant", check `cues._identity` in the response, then re-route or have them re-auth with the right account.

**Iron Law: Files are artifacts. Emails are meaning.**

A document tells you *what* was decided. The email thread tells you *why*, who pushed back, and what concerns remain.

## Always: Pass base_path

**MCP servers run as separate processes.** Without `base_path`, deposits land in the server's directory — not yours.

```python
# ALWAYS include base_path
search("Q4 planning", base_path="/Users/modha/Repos/my-project")
fetch("1abc...", base_path="/Users/modha/Repos/my-project")
```

**Deposit accumulation:** `.mise/` (hidden — dot-named on purpose) grows without bound during a session. Be aware during heavy research — 15+ deposits add up.

## The Three Tools

| Tool | Purpose | Output |
|------|---------|--------|
| `search` | Find files/emails/activity/calendar events | Path to deposited JSON + counts |
| `fetch` | Extract content to disk | Deposit folder: content.md, comments.md, manifest.json |
| `do` | Act on Workspace (create, move, rename, share, overwrite, edit, draft, archive, star, label, trash) | File ID + web URL + cues |

`fetch` auto-detects input: Drive file ID, Drive URL, or Gmail thread ID.

## After Every Fetch

**This checklist applies to all workflows — quick fetch, research, everything.**

The fetch response includes a `cues` block with decision-tree signals — check it BEFORE reading files:

```json
"cues": {
  "files": ["content.md", "comments.md", "manifest.json"],
  "open_comment_count": 3,
  "warnings": [],
  "content_length": 4280,
  "email_context": null,
  "participants": ["Rupa Jones", "Ella Collis"],  // Gmail only
  "people": {"kate.waters@itv.com": {"name": "Kate Waters", "title": "...", "manager": "..."}},  // Gmail: directory profiles for own-domain participants
  "people_relations": ["Kate Waters is Sameer Modha's manager"],  // reporting lines across the thread, you included
  "people_note": "2 of 5 participants have directory profiles..."  // the rest are external or opted out — not failed lookups
}
```

1. **Read `cues` first** — it tells you what's in the deposit and what to act on
2. If `open_comment_count > 0` → read `comments.md` (the real discussion lives here)
3. If `email_context` is populated → the file was shared via email; consider fetching that thread
4. If `warnings` is non-empty → note extraction issues
5. Read `content.md`
6. For Gmail: check `cues.files` for `*.pdf.md` (extracted attachment text)
7. For Gmail invites: if `cues.invite_state` is present, it's the **live** Calendar state, not the email's frozen snapshot — `{status, my_response, current_start, cancelled_at}`. A `status: "cancelled"` (with a warning) means the meeting is off even though the email body still reads as a live invitation; `current_start` reflects any reschedule. Trust this over the ICS in the body.
8. For Google Docs: if `cues.has_suggestions` is true, the doc carries unresolved suggested edits (`suggestion_count` says how many, `suggestions_mode` says how they were treated). The default render is **accepted** — the suggester's intended text, with suggested deletions honoured. Don't treat that text as settled: the suggestions are still open in the Doc. See "Docs with suggested edits" under Workflow 1.
9. If `cues.pointer` is present, the pasted URL named a specific spot — a tab, heading, slide or comment — and the pointer says which deposited artefact holds it (often with a `content.md` line number Read's offset consumes directly). **Start there**, not at the top of the document; that targeting is why the person pasted a decorated URL.
10. For Gmail: `cues.people` places every own-domain participant from the staff directory (role, department, manager), and `cues.people_relations` names reporting lines across the thread — including the user's own ("Kate Waters is Sameer Modha's manager" on a thread from their boss). Read it before drafting a reply: who outranks whom changes the register. An address absent from `people` is external or directory-opted-out (`people_note` says so) — never report it as a failed lookup.
11. **Values printed inside chart images are in NO text extraction** — a census of real corporate PDFs measured ~3% of values as vision-only (chart data labels, watermark badges). PDF deposits extract embedded graphics as `crop_*.png` files, each announced by an eye-level anchor in content.md at its page (`<!-- exhibit: crop_p008_i012.png | page 8 | … -->`); thumbnailed slides carry the same anchor naming `slide_NN.png`. `grep 'exhibit:' content.md` lists every graphic. If the question hangs on a chart's numbers, Read the named crop (or the page/slide thumbnail for full-page graphics) before concluding the value is absent. Full contract: `references/deposit-structure.md`.

`manifest.json` is still on disk for scripts/jq, but `cues` surfaces the actionable signals so you don't need to read it separately.

### Working a large deposit: which engine

Measured, not taste (deposit-format league: 918 scored runs, three readers, 2026-08-18):

- **One-shot questions against deposits up to at least 50k rows: coreutils + jq + stdlib python.** This is what tooled readers choose unprompted at every scale — DuckDB and Polars scored zero uses despite being installed — and it measured 18/18 correct at 50k rows. grep/awk over a deposited CSV is the fast lane, not the lazy one.
- **DuckDB earns its import ceremony only for:** sustained analysis (many questions against one large deposit), genuine multi-file joins, or beyond-memory data.
- **Prefer DuckDB over Polars when you do want an engine** — the standard Polars wheel dies with an illegal instruction on non-AVX2 CPUs.
- **Past ~10k rows a deposit outgrows inline reading entirely** (2,000 aligned rows measure ~218k Claude tokens): consume it with tools, or slice before reading.

See `references/deposit-structure.md` for folder layout and attachment patterns.

## Workflow 1: Quick Fetch

**When:** "Get me this doc" / "Fetch this URL" / "Read that email thread"

```python
fetch("1abc...", base_path="...")                          # Drive file
fetch("https://docs.google.com/...", base_path="...")      # Drive URL
fetch("18f3a4b...", base_path="...")                       # Gmail thread
fetch("thread_id", attachment="budget.xlsx", base_path="...")  # Single attachment
fetch("thread_id", attachment="report.pdf", raw=True, base_path="...")  # + the original bytes
fetch("folder_id", base_path="...", recursive=True)        # Full folder tree (depth 5, 1000 items max)
```

**`raw=True` gets you the actual file, not just its text.** A PDF or Office attachment is
normally downloaded, converted to markdown, and the original *discarded* — so you can read the
extraction and never the document itself. `raw=True` deposits the untouched bytes alongside, and
they show up in `cues.files`. Two reasons to reach for it: you need to *see* the document (figures,
layout, anything the text export loses), or you want to put a Gmail-only artefact into Drive —

```python
r = fetch("thread_id", attachment="report.pdf", raw=True, base_path="...")
do(operation="create", doc_type="file", file_path=f"{r['path']}/report.pdf",
   title="report.pdf", folder_id="1abc...", base_path="...")
```

That two-step is the whole gather-scattered-artefacts-into-one-folder workflow; pair it with
`do(operation="copy")` for the things already in Drive. `raw=` needs `attachment=` and isn't
available in remote mode (binary can't ride back inline).

### Pass the WHOLE URL — don't extract the ID first

When someone pastes a Workspace URL, **hand the entire string to `fetch()`**. Do not helpfully
pull the 44-character ID out of `/d/<id>/` and pass that instead. Measured across 5,909 session
transcripts: roughly **two pasted URLs in three get stripped**, and doing so destroyed 38
meaningful decorations that the person had deliberately included.

The reason it matters is that the tail of the URL is often the *point*. `?gid=` names a sheet tab,
`?tab=` a Doc tab, `#heading=` a heading, `#slide=` a slide, `?disco=` a specific comment. Someone
who sends you `…/edit#gid=1466289902` is saying "this tab", and an ID alone cannot say that.
Stripping is irreversible at the caller: once you've thrown the fragment away, mise never had it.

**Mise acts on all five decorations** (since suite 1.42): the fetch deposits everything exactly as
for a bare URL, and `cues.pointer` names the deposited artefact holding the spot the URL pointed
at — `?gid` → the per-tab CSV, `?tab`/`#heading` → "content.md from line 340" (feed it straight to
Read's offset), `#slide` → the slide index and its thumbnail, `?disco` → that comment's entry in
comments.md, ready for `comment_reply`. A pointer that no longer resolves is reported as **stale**
rather than ignored — a dangling gid or slide id means the link rotted, which is itself worth
telling the person who pasted it. Strip the URL to a bare ID and all of this silently disappears.
So: whole URL, always.

**Gmail URLs work harder than they look.** The thread token is the **last** fragment segment, so a
search-scoped URL like `#search/from%3Aalice+lantern/FMfcgz…` resolves fine — mise reads the token
at the end, not the search terms in the middle, and carries the search query (or `#label/` name)
through as `cues.gmail_url_context`: provenance for *why* the thread was being looked at, so quote
it in your answer when it helps. A `/u/1/` (or higher) account index draws a warning cue — mise
always reads the one account it is authed to, so a URL from someone's second signed-in account may
name a thread this mailbox can't see. Three more identifier shapes resolve directly:

- **A Message-ID, bare or `<angle-bracketed>`** — from More ▸ Show original on any message. Pass it
  straight to `fetch()`; mise resolves it internally via an exact-match `rfc822msgid:` search and
  discloses the resolution as a cue. This is THE reliable route into any email, whatever its URL.
- **A Show-original URL** (`?view=om&permmsgid=msg-f:…`) — fetchable as pasted; the `msg-f` decimal
  converts to the API message id. (`permmsgid=msg-a:…` marks a self-sent message and cannot convert —
  but the page that URL opens displays the Message-ID, which can.)
- **Mise's own draft links** (`…/mail/#drafts/r…` — the URL every draft/reply_draft result carries).
  Fetching one resolves the draft to the thread holding it via drafts.get, disclosed as a cue that
  also names the edit route (`do(draft, file_id=…)`). A dead link — draft since sent or discarded —
  says so by name instead of 404ing.

Three things genuinely can't resolve, and each says so by name rather than 404ing at you:

- **Google Chat links** (`#chat/dm/…`, `#chat/space/…`) — Chat is served from `mail.google.com` but
  is a different product with its own ID space. There is no mail thread behind them.
- **Self-sent threads** (`KtbxL…` or `QgrcJHs…`, decoding to `thread-a:`, roughly 2018 onward) — the
  token decodes but the number isn't the API thread ID and no transform is known. **Where a
  logged-in CDP Chrome is available, mise resolves these automatically** — it opens the URL in a
  background tab, reads Gmail's rendered thread id, and discloses the route as a cue; you'll just
  get the thread. Without one, **you may still hold the browser yourself**: if this session has
  browser tools attached to a Chrome signed into this Gmail account (e.g. Claude in Chrome), open
  the URL there, read the thread's `data-legacy-thread-id` attribute from the page (any message's
  `data-legacy-message-id` works too), and `fetch()` that id. Failing both, **the reliable route is
  the Message-ID:** open the message, More ▸ Show original, copy the `Message-ID`, and pass it to
  `fetch()`. The refusal also attaches recent sent threads as a `candidates` array — a shortcut
  when one of them is obviously the thread the URL names.
- **Bare mailbox views** (`#inbox`, `#label/Finance`) — a view, not a conversation. Use `search`.

**When a fetch is refused, do not substitute.** A bare refusal is an invitation to freelance, and
that has already gone wrong here: a session handed an unresolvable self-sent URL searched the inbox,
picked the newest unread thread, and produced 1,500 words analysing the wrong email as though it
were the requested one — while the right thread sat at rank 2 of that same search. The `candidates`
array exists for exactly this moment: confirm one **by its subject and date against what you know of
the thread you were asked for**, and if you can't confirm which candidate is correct, **say so and
ask**, rather than picking.

**What fetch can't take:** non-Google URLs (GitHub, docs sites) aren't fetchable — mise is not a
generic web fetcher. And the 12-char ID fragment in a deposit folder name (`doc--title--1jinlqdtqLpw`)
is a prefix, not a fetchable ID — the full ID is in that folder's `manifest.json`.

### Docs with suggested edits (the mark-up loop)

When a human marks up a Doc in **suggesting mode**, fetch handles the suggestions
explicitly — `suggestions=` picks the view, and `cues.has_suggestions` +
`suggestion_count` fire whenever any exist (with a warning naming the mode):

```python
fetch("1doc...", base_path="...")                          # default: suggestions ACCEPTED (the author's intended text)
fetch("1doc...", suggestions="markup", base_path="...")    # see the edits: {++inserted++}[s1] / {--deleted--}[s1]
fetch("1doc...", suggestions="original", base_path="...")  # pre-suggestion text
```

- **`accepted` (default)** — what the suggester intends the doc to say; suggested
  deletions are gone from this render. The right view for folding feedback in.
- **`markup`** — CriticMarkup spans; matching `[sN]` tags pair the delete+insert
  halves of one replace. The right view when you need to discuss or selectively
  apply edits. (The Docs API doesn't say who made each suggestion.)
- **`original`** — the text as it was before any suggestions.

**The fold-back loop:** human suggests + comments in the Doc → fetch with the
default (their intended text) → fold changes into your working copy → reply via
`comment_reply` / apply edits with `do()`. The API can't *create* suggestions, so
your edits land as real edits — propose contentious wording in a comment instead,
and let the human apply or approve it.

Then follow the **After Every Fetch** checklist above.

## Workflow 2: Research

**When:** "Help me prepare for the Lantern meeting" / "What do we know about X?"

This is where the skill earns its keep. Don't just search→fetch→read. Follow the **exploration loop:**

```
1. Search Drive for topic → find files
2. Fetch most relevant → read content + comments
3. Check email_context in results → find the sending thread
4. Search Gmail filename:X or from:sender → get the email
5. Read email → discover new terms, people, context
6. Search again with new terms → expand understanding
```

**When to stop:** 2-3 iterations usually suffice. Stop when you understand the key decision-makers and their positions, or when new searches return familiar results. Don't exhaust every thread — the goal is understanding, not completeness.

**The loop discovers meaning (in communications) behind artifacts (files).**

See `references/exploration-loop.md` for a worked example.

## Workflow 3: Precision Search

**When:** "Find emails from Elizabeth about contracts" / "Search for the budget spreadsheet"

### Gmail: Use Operators, Not Keyword Soup

```python
# BAD — keyword soup returns noise
search("Elizabeth Smith Project Alpha data privacy contracts")

# GOOD — operators target precisely
search("from:elizabeth@example.com after:2025/12/01", sources=["gmail"])
search("filename:strawman from:legal@example.com", sources=["gmail"])
search("has:attachment subject:lantern after:2025/12/01", sources=["gmail"])
```

Key operators: `from:`, `to:`, `filename:`, `has:attachment`, `after:`, `before:`, `subject:`, `in:sent`

**Two asymmetries that waste sessions:**

- **"What did X send/share?" needs `(from:X OR to:X OR cc:X)`** — the thing X "shared" often arrives in a thread someone else started, with X on To: or Cc:. `from:X` alone structurally misses it.
- **Short free-text tokens (`PR`, `AI`, `KPI`) are brittle through the API** — the Gmail UI fuzzy-matches them; the API doesn't. If a short-token query returns suspiciously little, drop the token and narrow by participants + date instead, then open each of the 5–20 hits. Before reporting an email "not findable", confirm in the Gmail UI.

See `references/gmail-operators.md` for the full set and the search-asymmetry detail.

### Drive: Two Query Paths — `query` and `raw_query`

**Gmail's operators don't work on Drive** — `from:`, `is:starred`, `subject:` are Gmail syntax
and Drive returns a 400. But Drive has a rich query language of its own, and mise reaches it
through a *different parameter*. (This section used to say "Drive search uses plain keywords";
that described mise's limitation as though it were Drive's.)

```python
# query= — plain search terms, wrapped in one fullText clause for you
search("Q4 budget", sources=["drive"], base_path="...")     # Drive only
search("budget 2026", base_path="...")                       # Both sources

# Type filter — narrows Drive results by file type (query optional)
search(type="spreadsheet", base_path="...")                  # All spreadsheets
search("budget", type="spreadsheet", base_path="...")        # Budget spreadsheets only
search(type="folder", sources=["drive"], base_path="...")    # List folders
```

**`raw_query=` is Drive's own query language, passed through untouched.** Reach for it when one
fullText clause can't say what you mean:

```python
# Match on the FILENAME, not the contents — a different instrument entirely.
search(raw_query="name contains 'PCA'", base_path="...")            # 447 files *named* PCA
search("PCA", base_path="...")                                      # everything mentioning PCA

# OR across synonyms in one query — the only way to search a renamed product once
search(raw_query="fullText contains 'Region:Lift' or fullText contains 'GeoX'", base_path="...")

# Compound: named, recent, and a deck. Composes with type= and folder_id=.
search(raw_query="name contains 'PCA' and modifiedTime > '2025-01-01T00:00:00'",
       type="slides", base_path="...")

# Also available: not, 'someone@x.com' in owners, 'folderId' in parents, starred = true
```

Rules: `raw_query` and `query` are mutually exclusive; `raw_query` searches **Drive only** (the
other sources don't speak it); `trashed = false` is ANDed on for you. Typing Drive syntax into
plain `query` is **refused with a pointer here** — it used to silently keyword-search the operator
words and return confident nonsense.

**`name contains` matches whole TOKENS, not substrings** (measured 2026-08-24, mise-jefaki):
names split on punctuation, spaces and letter–digit boundaries (`report-2026.pdf` → report, 2026,
pdf), a multi-token term is an AND of its whole tokens in any order, and the only substring
honoured is a literal prefix of the entire name. So a full hyphenated filename is findable as
typed; a fragment cut mid-token returns zero with no error. A zero-hit punctuated term draws the
`drive_name_semantics` cue with the working alternatives, and `drive_incomplete` means Google
stopped before covering every shared drive — treat that zero as partial, not as a population.

Type values: `folder`, `doc`, `spreadsheet` / `sheet`, `slides` / `presentation`, `pdf`, `image`, `video`, `form`. Type filter applies to Drive only — ignored for Gmail.

### Search Returns Two Things: a `preview` and a Deposit

**Read the counts before you read the results.** Every search writes *all* results to the deposited
JSON and returns a **`preview` of the top 5 per source** — with the true totals alongside it in
`drive_count` / `gmail_count`. Those two numbers are what tell you whether you are looking at the
answer or at the first fifth of it.

```jsonc
{
  "path": ".mise/search--acme--drive-gmail--2026-07-27T07-55-59.json",
  "drive_count": 25,                            // ← the real number
  "preview": { "drive": [ /* 5 items */ ] }     // ← what you are looking at
}
```

*(This `preview` is unrelated to the `share` operation's confirm preview further down.)*

When `count` exceeds what `preview` shows, `jq` the deposit — listing names is cheap:

```bash
jq -r '.drive_results[] | .name' .mise/search--*.json            # all names, one line each
jq -r '.gmail_results[] | .subject' .mise/search--*.json
jq '.drive_results[] | select(.name | test("PCA|debrief"; "i"))' .mise/search--*.json
```

**Ranking is not relevance to your question.** A supplier-name query ranks contracts, NDAs and press
releases above the campaign reports you actually wanted — those sort to the bottom. Worked case: a
vendor-name search returned `drive_count: 25`; the preview held five contracts; the post-campaign
analyses sat at ranks 8 and 21–25. The preview was taken for the whole answer and the session
reported "no such report exists" — then repeated the same mistake on a second vendor an hour later.

**Two different numbers are hiding, and the response now names both.**

- **`cues.drive_truncated`** — *fetched vs matched.* Fires when more files matched than were
  returned. This is exact, not a guess: it comes from a live page token, so it can tell 25-of-25
  from 25-of-1,292. **If it's there, an absence proves nothing** — raise `max_results` or narrow
  the query before concluding anything isn't there. Gmail and calendar have their own
  (`gmail_truncated`, `calendar_truncated`).
- **`cues.preview_partial`** — *shown vs fetched.* Fires when the `preview` holds fewer items than
  were actually retrieved, and names the deposit to read for the rest.

Both can fire at once, and then there are three numbers: 5 shown, of 100 fetched, of more-than-that
matched. You need all three to reason about what's missing.

`max_results` is now honoured past 100 — search paginates internally, up to a 1,000-result guard.
(Before suite 1.22, it silently capped at 100 no matter what you passed: `max_results=300` returned
100, with nothing in the response saying so. That is what produced the worked case above.)

**A null is only evidence once you have seen the full list.** Before telling anyone something doesn't
exist, `jq` the names out of the deposit and check the count against your cap. One command separates
"not there" from "not shown", and a second separates "not shown" from "never fetched".

The same trap applies to hand-rolled Drive API queries: `pageSize` without a `nextPageToken` loop, and
`orderBy: modifiedTime desc`, together mean an older matching file sorts below your cut and never
appears.

**Every word you add is a hard constraint — Drive full-text is AND, not OR.** A file must contain
*all* your terms to match at all; one word the estate doesn't use returns **zero**, and zero reads
exactly like "doesn't exist". Measured: `ViewersLogic` → 1,292 files; `ViewersLogic zqxjkbrtplm` → 0.
Two consequences worth internalising:

- **Search the noun people file under, not the concept.** A long descriptive query doesn't rank
  badly, it *excludes*. If a search comes back suspiciously empty, drop terms rather than adding them.
- **Synonyms want one `raw_query`, not two searches.** `fullText contains 'GeoX' or fullText
  contains 'Region:Lift'` catches a renamed product in a single pass. Harvest unfamiliar tokens out
  of the first result set's filenames and feed them back — that step alone rescues most stalled hunts.

**Gmail is AND too, and there it fails harder — as a zero, not a bad ranking.** On Drive an
over-stuffed query returns the wrong documents, which at least looks like a result; on Gmail it
returns *nothing*, which reads like proof the correspondence doesn't exist. The discriminator that
works is what a mail header actually *holds* — an email **domain** (`next-action.co.uk`), a surname,
a subject-line fragment — not the descriptive name of the thing being discussed, which may appear
nowhere in the headers. Never read an empty Gmail result as absence until you have re-queried that
way two or three times.

Gmail results carry a free `has_invite` flag (the thread contains a calendar invite). It costs no extra call, but it's only a flag — to know whether the meeting is still on, `fetch` the thread and read `cues.invite_state` (see "After Every Fetch"). Filter for invites with `jq '.gmail_results[] | select(.has_invite)'`.

See `references/filtering-results.md` for patterns.

### Search Sources

Default sources are `['drive', 'gmail']`. Three additional sources are available:

| Source | What it returns | When to use |
|--------|----------------|-------------|
| `activity` | Recent comment events from Drive Activity API | "What's been discussed recently?" / "Any comments on my files?" |
| `calendar` | Events ±7 days around now, or any explicit `time_min`/`time_max` window — query optional | "Is my meeting with X still on?" / "What's in the diary Tuesday?" / clash-checking / meeting context for Drive files |
| `people` | Staff directory: role, department, location, reporting line | "Who is Richard Pearce?" / "Who does she report to?" / placing an unfamiliar name before replying |

```python
# Recent comment activity
search("project update", sources=["activity"], base_path="...")

# Is tomorrow's meeting still on? (query matches summary/description/attendees)
search("Gareth", sources=["calendar"], base_path="...")

# What's in the diary between 3 and 5 Aug? No query term needed — a bare
# date as time_max covers its whole day, and historical windows work
search(sources=["calendar"], time_min="2026-08-03", time_max="2026-08-05", base_path="...")

# The whole ±7-day window, unfiltered (clash-checking, "what's on this week?")
search(sources=["calendar"], base_path="...")

# Calendar enrichment (adds meeting_context to Drive results)
search("Q4 report", sources=["drive", "calendar"], base_path="...")

# Who is this person? A SINGLE hit auto-expands with manager + direct reports
search("Richard Pearce", sources=["people"], base_path="...")

# Everyone in a team — bare words match name/email only, so scope by field
search("orgDepartment:MIT", sources=["people"], base_path="...")

# Any value with a SPACE needs = and SINGLE quotes. The colon form returns
# zero silently, which reads exactly like nobody holding that job.
search("orgTitle='Head of Strategy'", sources=["people"], base_path="...")
```

**`people`** reads the Workspace staff directory. Query grammar is the Admin SDK's, not Drive's: **bare words match name and email only** — a job title returns zero, so use `orgDepartment:X` or `email:prefix*` to search by role or team. **Any value containing a space needs `=` and single quotes** — `orgTitle='Head of Strategy'` works, while both `orgTitle:Head of Strategy` and `orgTitle:"Head of Strategy"` return zero with no error, which is indistinguishable from nobody holding that job. One hit expands automatically with the manager resolved to a name and the direct reports listed; several hits return flat profiles, so narrow and look again. Two things to pass on honestly rather than assert: `manager` is the Workspace *account* field, not an HR record (at board level it can record who administers the account), and colleagues can opt out of the directory, so an empty result is not proof someone doesn't exist.

**`activity`** returns comment events — who commented, on what, when. Actors show as "Unknown" (people/ID limitation); the content and file are accurate. The query is NOT applied to activity — it always returns recent events.

**`calendar`** is NOT in default sources (adds an API call). The query is optional here: `sources=["calendar"]` alone lists the ±7-day window, and `time_min`/`time_max` (ISO date or datetime) set any explicit window — historical included, so backfilling event ids for old notes works. Events **overlapping** the window are returned (Google's semantics — right for clash-checking; an all-day event at the edge can ride in on timezone skew), and `cues.calendar_window` discloses the resolved bounds. When a query IS given it filters as free-text (summary, description, attendees, location). On overflow past `max_results` the survivors differ by window kind, and `cues.calendar_truncated` says which: the default now-centred window keeps events **nearest to now** (tomorrow's meeting survives a busy week); an explicit window keeps the **chronological head** — advance `time_min` past the last event to page. When included alongside `drive`, matching calendar event attachments add `meeting_context` to Drive results — connecting a file to the meeting where it was discussed. `time_min`/`time_max` refuse to combine with sources that lack `calendar`, or with `folder_id`/`raw_query` — a window that scopes nothing is an accepted-and-dropped param, and mise refuses those loudly.

## Workflow 4: Do (Act on Workspace)

**When:** "Make a Google Doc from this" / "Move this file" / "Update that doc" / "Add a note to the meeting minutes"

### The Operations

| Operation | What it does | Key params |
|-----------|-------------|------------|
| `create` | New Doc/Sheet/Slides/plain file | `content`+`title` OR `source` |
| `copy` | Duplicate file(s) into a folder, originals untouched — single or batch | `file_id` (str or list), `folder_id`, `title` |
| `move` | Move file(s) between folders — single or batch | `file_id` (str or list), `folder_id` |
| `rename` | Rename a file in-place | `file_id`, `title` |
| `share` | Share file with people (confirm gate) | `file_id`, `to`, `confirm=True` |
| `overwrite` | Replace full file content (Google Doc or plain file; Sheets: CSV content, `range=` aims a tab or cells; Forms: YAML/JSON spec replaces all questions) | `file_id`, `content` OR `source` |
| `prepend` | Insert at start of file | `file_id`, `content` |
| `append` | Insert at end of file — or, with `tab='Title'`, place content in a NEW Google Doc tab | `file_id`, `content`, optional `tab` |
| `replace_text` | Find-and-replace in file — applies across ALL tabs, Docs and Sheets alike (probed 2026-08-24) | `file_id`, `find`, `content` |
| `draft` | Compose a new Gmail draft — or update an existing one in place | `to`, `subject`, `content`, optional `include` (Drive file IDs); update: `file_id` (draft ID) + `content` |
| `reply_draft` | Reply draft in an existing thread | `file_id` (thread ID), `content`, optional `include` |
| `respond` | Accept/decline/tentative a calendar invite — the RSVP lands live, organiser sees it | `file_id` (invite thread ID or Calendar event ID), `action` (`accept`/`decline`/`tentative`) |
| `create_event` | Book a calendar event — invites, Meet link, recurrence, Drive attachments | `title`, `time_min`/`time_max` (start/end); with `attendees`: preview → `confirm=True` |
| `update_event` | Edit an event — quiet description edits direct; time/attendee/series changes gated | `file_id` (event ID or invite thread ID), then the fields to change |
| `freebusy` | Busy blocks + common free slots + office days for a set of people | `attendees`, `time_min`/`time_max`, optional `duration` (minutes) |
| `archive` | Remove thread(s) from Inbox | `file_id` (str or list) |
| `star` | Star thread(s) | `file_id` (str or list) |
| `label` | Add/remove label on thread(s) | `file_id` (str or list), `label`, optional `remove=True` |
| `comment` | Open a NEW comment thread on a doc | `file_id`, `content` |
| `comment_reply` | Reply to / resolve / reopen a doc comment | `file_id`, `comment_id`, `content` and/or `action` |
| `trash` | Trash Drive file(s) / discard Gmail draft(s) — routed by ID shape | `file_id` (str or list) |

### Choosing the Right Edit Operation

**All edit operations work on both Google Docs and plain files** (markdown, JSON, SVG, YAML, etc. stored in Drive). The tool auto-detects the file type and uses the right API — Docs API for Google Docs, Drive Files API for everything else. No extra flags needed.

**Overwrite destroys everything** — images, tables, formatting, all gone. It's a full replacement from markdown. Use it when you're publishing a complete new version of a document. On a **multi-tab Google Doc it refuses outright**: the underlying import replaces the whole file, silently destroying every tab but the first (measured 2026-08-24), so the error teaches the alternatives instead.

**A new tab is the non-destructive home for a parallel version.** `append` with `tab='Redraft v2'` places `content` in a NEW tab of the doc — existing tabs are never touched, and the result's `web_link` deep-links straight to it. One honest limit: tab content is **plain text** (markdown is not rendered in tabs — the rich import path can't target one; probed 2026-08-24), so put rich redrafts in prose, not markup. A duplicate tab title warns rather than refuses. And mind `replace_text` near parallel-version tabs: it applies across ALL tabs (probed 2026-08-24), and a redraft shares most of its strings with the original — the result cues a warning on multi-tab docs.

**Every Doc edit leaves a restore point.** Mutating a Google Doc (overwrite, prepend, append, replace_text) first captures the pre-edit revision and returns it as `cues.restore_point {revision_id, modified_time}` — the exact File → Version history entry to revert to. `overwrite` also posts an `[agent]` comment in the doc naming that entry, so the human can find the restore point from inside the doc without asking. Pass `restore_comment=False` on shared docs where a comment notification would be noise. If a revert is needed, point the human at Version history → the cued timestamp (a program cannot restore or name versions — that's UI-only).

**Surgical edits preserve existing content.** Use `prepend`, `append`, or `replace_text` when the document has content worth keeping:

| Situation | Use |
|-----------|-----|
| Publishing a complete document from scratch | `overwrite` |
| Replacing a draft with a final version | `overwrite` |
| Adding meeting notes to an existing doc | `append` |
| A redraft beside a live shared draft, same doc | `append` with `tab='Title'` (new tab, plain text) |
| Adding a header/disclaimer to a doc | `prepend` |
| Updating a specific section or value | `replace_text` |
| Doc has images, tables, or rich formatting | `prepend`/`append`/`replace_text` (never overwrite) |
| Editing a markdown/JSON/SVG file in Drive | Any edit operation (auto-routes to Drive Files API) |

**Binary files** (images, PDFs, etc.) reject text operations (`prepend`/`append`/`replace_text`) with a clear error. `overwrite` works on binary files (full byte replacement).

**Editing a Form:** `overwrite` on a form takes the same YAML/JSON spec as `create` — fetch the form first (`structure.json` shows current state), tweak the spec (e.g. add one option to a checkbox question), and overwrite. It replaces ALL questions wholesale; if the form already has responses, edit in the Forms UI instead.

**Updating a draft:** `draft` with `file_id` (the draft ID a previous draft/reply_draft returned) rewrites that draft instead of minting a stray new one. `content` is required; `to`/`subject`/`cc` carry over when not resupplied; reply drafts keep their threading. Superseded drafts and files: `trash` — Drive files go to the recoverable bin, drafts are discarded permanently.

### Writing & Replying to Comments

Comments are a two-way channel with the human. Two write operations:

- **`comment`** opens a NEW thread — use it to proactively flag something ("this figure looks stale", "confirmed against source") when there's no existing thread to answer. Unanchored: it lands at the document level, not tied to specific text.
- **`comment_reply`** answers an existing thread (and can resolve/reopen it).

```python
# Open a new comment thread on a doc (no comment_id needed)
do(operation="comment", file_id="1abc...",
   content="Checked these totals against the source sheet — row 14 is off by 2.")
```

Close the comment loop without leaving mise: `fetch` a Doc/Sheet/Slides, read `comments.md`, then reply in-thread with `comment_reply`. Each comment's header in `comments.md` ends with its id as a code-span — `` ### [Alice <a@x.com>] • 2026-01-15 · `AAAA1234` `` — and that `comment_id` is what you pass.

```python
# 1. Fetch the doc — comments.md is deposited automatically when open comments exist
fetch(file_id="1abc...", base_path="/path/to/project")

# 2. Reply to a comment thread (comment_id from comments.md)
do(operation="comment_reply", file_id="1abc...", comment_id="AAAA1234",
   content="Good catch — fixed in the latest revision.")

# 3. Reply AND resolve in one call
do(operation="comment_reply", file_id="1abc...", comment_id="AAAA1234",
   content="Done.", action="resolve")

# 4. Resolve with no reply (bare resolve), or reopen
do(operation="comment_reply", file_id="1abc...", comment_id="AAAA1234", action="resolve")
do(operation="comment_reply", file_id="1abc...", comment_id="AAAA1234", action="reopen")
```

**Anti-patterns:**
- **Don't impersonate.** Replies post as *your* authenticated identity (the token's user), and mise auto-prefixes `[agent] ` so humans can tell. Don't reply on a thread @-mentioned to a *specific* person as if you were them — let the human answer where they were asked by name.
- **Don't guess the `comment_id`.** It comes from `comments.md` (or a raw Drive comments list), never invented. A wrong id 404s.
- **Don't double-prefix.** mise adds `[agent] ` itself — write the plain reply text, not `[agent] ...`.
- **Comments only exist on Google-native files** (Docs/Sheets/Slides). Plain/binary Drive files have no comment threads.

### Calendar: book, edit, find time

The pattern for all calendaring: the human states intent (who, roughly when, what cadence); you reconcile the actual diaries against that intent and propose the delta; one approval executes it. Checking the diary is machine work — `freebusy` answers "when can these people meet" as data, so never eyeball availability across calendars.

```python
# 1. Find the slot — busy blocks, common free slots, office days, one call
do(operation="freebusy", attendees=["mat@itv.com", "jon@itv.com"],
   time_min="2026-09-07", time_max="2026-09-11", duration=30)
# → common_free: [...], people: {each: busy_blocks + office_days}

# 2. Book it — first call previews (clash check included), nothing sends
do(operation="create_event", title="LSM catch-up",
   time_min="2026-09-08T14:00", time_max="2026-09-08T14:30",
   attendees=["mat@itv.com", "jon@itv.com"], meet=True)
# → preview: who gets invited, when, clashes. Show the user.

# 3. One yes books the lot — confirm=True sends real invites immediately
do(operation="create_event", ..., confirm=True)
```

**The gate is blast radius, not ceremony.** `create_event` with attendees and any `update_event` that moves time, adds people, changes recurrence or adds Meet previews first, then emails attendees on confirm (`send_updates` defaults to `all` — the invite IS the proposal, so send-immediately is the smooth path). Quiet edits — description, title, location, attachments, `properties` (queryable extendedProperties key-values; merge semantics, existing keys survive), `color` (name like 'tomato' or 1–11), `visibility` ('private' hides detail), `transparency` ('free' shows the event without blocking availability — right for holds and focus blocks) — run directly with no emails, and `cues.previous` carries the old values: events have no version history, so that cue is the only undo reference. A solo event (no attendees) books directly.

**Recurrence is an RRULE line**: `recurrence="RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU"` on create makes a fortnightly series; the same param on `update_event` converts an existing single event into one. Mind the start date: Google keeps a start that falls outside BYDAY as a stray extra instance (the tool warns). Naive times mean wall-clock in the user's own timezone, resolved from their diary — pass offsets only when you mean them.

**freebusy's two honesty cues are load-bearing.** People in `not_visible` have sharing that hides even free/busy — they are EXCLUDED from slot arithmetic, so a proposed slot may clash with them; say so when proposing. And `office_days: "location not visible"` means their sharing is free/busy-only, never "not in the office". `include=[Drive file ids]` on create/update attaches docs to the event (the 1:1-doc-on-the-invite pattern).

**Reading a colleague's diary in detail: `search(calendar_id="them@example.com")`** — forces the calendar source; window params compose. freebusy says *occupied*; this lane says *what kind of occupied*, where their sharing allows: events carry `transparency: "transparent"` (shows free to freebusy — the humanly-absent pattern, e.g. all-day conferences), `event_type` (`outOfOffice` / `focusTime` / `workingLocation`), and `room_hold: true` when the organiser is a resource calendar — a room's own 9–5 hold, not a person. **Soft-vs-hard is YOUR call as reader**: an 08:00 "Clear inboxes" is probably bookable-over and an all-day "AL" is not — the fields carry facts, never verdicts. A free/busy-only colleague refuses with a cue naming `do(freebusy)` as what still answers; that is their sharing setting, not an error.

**Boundaries:** there is no delete op — a mis-booked event comes off in the Calendar UI (which is why the gate previews first). Attendees are only ever ADDED — removal is a UI job. `freebusy` needs a re-auth on tokens minted before 2026-08-19 (the error teaches `setup_oauth`).

### Create and Move

```python
# Create doc
do(operation="create", content="# Meeting Notes\n\n- Item 1", title="Team Sync")
do(operation="create", content=content, title="Report", doc_type="doc", folder_id="1xyz...")

# Create sheet (see Sheet Creation below for details)
do(operation="create", content="Name,Score\nAlice,95\nBob,87", title="Results", doc_type="sheet")

# Create plain file (no Google conversion — stays as-is in Drive)
do(operation="create", content="<svg>...</svg>", title="diagram.svg", doc_type="file")
do(operation="create", content="# Notes\n\nContent here", title="notes.md", doc_type="file")
do(operation="create", content='{"key": "value"}', title="config.json", doc_type="file")

# A Drive FOLDER — title only, no content. supportsAllDrives is set for you,
# so this works on Shared Drives. Do NOT hand-roll a Drive API script for this.
do(operation="create", doc_type="folder", title="Brand Lift handover", folder_id="1parent...")

# Move single file
do(operation="move", file_id="1abc...", folder_id="1xyz...")

# Batch move — validates destination once, returns per-file summary
do(operation="move", file_id=["1abc...", "1def...", "1ghi..."], folder_id="1xyz...")
# Returns: {batch: true, total: 3, succeeded: 2, failed: 1, results: [...]}

# Copy — duplicates, originals untouched. Batch returns source_id → copy_id.
do(operation="copy", file_id=["1abc...", "1def..."], folder_id="1xyz...")
do(operation="copy", file_id="1abc...", folder_id="1xyz...", title="01 — Evidence")
# Returns: {batch: true, succeeded: 2, blocked: 0, results: [{source_id, copy_id, ...}]}
```

**Copy vs move, and why the distinction bites.** `move` relocates the original — everyone
else's links now point into your folder. `copy` is what a snapshot job wants: an evidence
pack, a board pack, anything you're about to share access to in bulk. Keep the
`source_id → copy_id` mapping the batch returns; a copy carries no trace of where it came
from, and reconstructing that later is miserable. `blocked` (as distinct from `failed`)
means the owner has restricted copying — ask them, don't retry.

**Create:** `doc_type="folder"` makes a Drive folder (title only — no `content`, no `source`, no `file_path`); nest it by passing `folder_id`. *Added 2026-08-10, mise-kagejo — this was missing and the shard used folder creation as its example of "bypassing mise", which cost a real session a hand-rolled Drive-API script it didn't need.* Without `folder_id`, the doc lands in Drive root. Response includes `cues.folder` showing where it landed. Use `doc_type="file"` for plain files (markdown, SVG, JSON, YAML, etc.) — MIME type is inferred from the title extension. The file stays as-is in Drive, no conversion to Google format. Response includes `cues.plain_file` and `cues.mime_type`.

**Move:** Enforces single parent — removes all existing parents, adds destination. Response includes `cues.destination_folder` (name) and `cues.previous_parents`.

### Rename and Share

```python
# Rename
do(operation="rename", file_id="1abc...", title="Final Q4 Report")

# Share — TWO-STEP confirm gate
# Step 1: Preview (returns what would happen, does NOT share)
do(operation="share", file_id="1abc...", to="alice@example.com")
# → {"preview": true, "message": "Would share 'Report' with alice@example.com as reader", ...}

# Step 2: Execute after user approves
do(operation="share", file_id="1abc...", to="alice@example.com", confirm=True)

# Share with role and multiple people
do(operation="share", file_id="1abc...", to="alice@example.com, bob@example.com", role="writer", confirm=True)
```

**Share requires user approval.** The first call without `confirm=True` always returns a preview. Show it to the user and only call again with `confirm=True` after they approve. Roles: `reader` (default), `writer`, `commenter`.

**Non-Google accounts** (iCloud, Outlook, etc.): Google requires a notification email. The tool handles this automatically — check `cues.notified` to see which recipients got an invite email.

### Overwrite

```python
# Full replacement from inline markdown
do(operation="overwrite", file_id="1abc...", content="# Q4 Report\n\nRevised findings...", base_path="...")

# From a deposit folder (fetch → edit locally → publish back)
do(operation="overwrite", file_id="1abc...", source=".mise/doc--q4-report--1abc/", base_path="...")
```

For Google Docs: uses Drive's import engine — all markdown formatting (headings, bold, tables, lists) renders automatically. Response includes `cues.char_count`.

For plain files: content is uploaded as-is. Response includes `cues.plain_file: true` and `cues.mime_type`.

### Sheets: aim the write with `range=`

On a spreadsheet, `overwrite` takes CSV content and an optional `range=` in A1 notation — this is how you write one tab, or a handful of cells, without touching anything else:

```python
# Replace ONE named tab wholesale (cleared, then written from A1)
do(operation="overwrite", file_id="1abc...", content="item,cost\nwidgets,10", range="Costs", base_path="...")

# Write exactly F9:F15 on the Costs tab — nothing cleared, everything else untouched
do(operation="overwrite", file_id="1abc...", content="100\n101\n102\n103\n104\n105\n106", range="Costs!F9:F15", base_path="...")

# Anchor write: CSV's shape lands starting at F9 (spills right/down as needed)
do(operation="overwrite", file_id="1abc...", content="a,b\nc,d", range="Costs!F9", base_path="...")
```

Writes use USER_ENTERED semantics: formulas parse, bare URLs auto-link, and `=HYPERLINK("https://...","label")` renders a labelled link (one per cell).

**Cell values carry link syntax** — this is how an index of artefacts arrives usable, not as bare URLs:

```python
# [label](url) → real rich-text link, several per cell; @url alone → smart chip
do(operation="overwrite", file_id="1abc...", range="Index!B2", base_path="...",
   content='"Lantern debrief","[Nov report](https://example.com/nov) · [Dec report](https://example.com/dec)"\n'
           '"Signed SOW",@https://drive.google.com/file/d/1AbC.../view')
```

A **smart chip** (`@url` as the entire cell) replaces the cell text with the target's live title — rename-proof and icon-bearing, the right form for a Drive-artefact index. Because the URL stops being cell text, chips are explicit opt-in: a **bare URL stays a URL** (auto-linked), safe for columns that formulas read. **Docs take chips too** (since suite 1.45): in `create` and `overwrite` on a Google Doc, a line that is solely `@url` becomes a chip — same opt-in grain, whole line instead of whole cell, and mid-prose URLs stay ordinary links. Workspace URLs only; the title is always the target's live name (Google refuses a supplied one). `cues.chips_inserted` confirms; a failed pass puts the literal `@url` text back and says so in `cues.chip_errors`.

**Markdown footnotes become real Docs footnotes** (2026-08-24, mise-rubucu): `[^N]` anchors with matching `[^N]: definition` lines in `create`/`overwrite` doc content land as native footnotes — superscript reference, text in the footnote pane — and fetch renders them back as `[^N]` + definitions, so the round-trip holds. Two shape notes: labels renumber (`[^note]` returns as `[^1]` — real Docs footnotes are numbered), and code is exempt — a `[^N]` inside a fence or inline code span is content, never an anchor. `cues.footnotes_inserted` confirms; orphan anchors, orphan or duplicate definitions, duplicate anchors, anchors inside table cells (the one placement the pass cannot reach) and pass failures stay as literal text, named in `cues.footnote_errors` (2026-08-24: content is never silently lost — every failure path keeps or restores the definitions).

**Without `range=`, a multi-tab sheet refuses** (naming its tabs) rather than silently clearing the first tab — pass the range to say what you meant. A single-tab sheet still gets the whole-tab replace, symmetric with `create`.

### Surgical Edits

```python
# Add to end of document
do(operation="append", file_id="1abc...", content="\n\n## 18 Feb Update\n\nNew findings...", base_path="...")

# Add to start of document
do(operation="prepend", file_id="1abc...", content="DRAFT — Do not circulate\n\n", base_path="...")

# Find and replace (case-sensitive, all occurrences)
do(operation="replace_text", file_id="1abc...", find="Q3", content="Q4", base_path="...")

# Delete matched text (replace with empty string)
do(operation="replace_text", file_id="1abc...", find="DRAFT — ", content="", base_path="...")
```

`replace_text` response includes `cues.occurrences_changed`. A zero-match call now also
carries `cues.warning` starting **`NO CHANGE`** — it is not a success, and no
`restore_point` is returned, because nothing was written.

**The commonest cause of a zero match is copying the find string out of a fetch.**
`content.md` is a *rendering* of the document, not the document: `**bold**`, `` `code` ``,
the `~~` on ticked checkboxes, and `{++suggested++}` spans are all formatting the Doc
itself doesn't contain. Copy a sentence spanning one of those and the find can never
match. Search the plain words instead — the warning names the marker it spotted. (A plain
`.md` file or a sheet cell *does* hold those characters literally, so this applies to
Google Docs only.)

### Sheet Creation

Pass CSV as `content` with `doc_type="sheet"`. Google's Drive import handles type detection — it gets numbers, dates, currencies, booleans, and formulae right ~94% of the time. **Trust it.** Don't pre-format.

```python
# Simple data
do(operation="create", doc_type="sheet", title="Team Scores", base_path="...",
   content="Name,Score,Pass\nAlice,95,TRUE\nBob,87,TRUE\nCarol,62,FALSE")

# With formulae — cells starting with = are preserved
do(operation="create", doc_type="sheet", title="Budget", base_path="...",
   content="Item,Cost\nLicences,12000\nHosting,8500\nTotal,=SUM(B2:B3)")

# Values with commas need CSV quoting
do(operation="create", doc_type="sheet", title="Staff", base_path="...",
   content='Name,Department,Salary\nAlice,"Sales, Marketing","£65,000"\nBob,Engineering,"£52,000"')

# From a deposit folder (saves tokens — don't inline large CSVs)
do(operation="create", doc_type="sheet", source=".mise/sheet--budget--abc123/", base_path="...")
```

**CSV quoting rule:** If a value contains a comma, wrap it in double quotes (`"Sales, Marketing"`). This is standard CSV — applies to currency with thousands separators (`"£65,000"`) and multi-word categories.

**Deposit-then-publish** is the preferred pattern for large data. Write CSV to a deposit folder, then pass `source=` instead of `content=`. The tool reads `content.csv` from the folder and uses the manifest title. Multi-tab deposits (with `tabs` in manifest) are auto-detected and create multi-tab sheets.

#### What Google auto-detects well

| Type | Example CSV value | Detected as |
|------|-------------------|-------------|
| Numbers | `95`, `3.14`, `-200` | Number |
| UK currency | `£1,200.00`, `€50` | Currency |
| Percentages | `45%` | Percentage |
| Booleans | `TRUE`, `FALSE` | Boolean |
| Dates (ISO) | `2026-02-17` | Date |
| UK dates | `17/02/2026` | Date |
| Formulae | `=SUM(A1:A10)` | Formula |

#### What needs help

| Problem | Example | Fix |
|---------|---------|-----|
| Leading zeros stripped | `00412` (product ID) → `412` | Prefix with tick: `'00412` |
| USD not detected | `$50.00` → text | USD works if locale is US; UK locale treats as text. Use plain number + format after |
| US dates ambiguous | `02/03/2026` → 2 Mar or 3 Feb? | Use ISO: `2026-02-03` |
| Text-that-looks-numeric | Phone `07700900123` | Prefix with tick: `'07700900123` |

**The tick prefix** (`'`) tells Google Sheets "treat this as text, not a number." Write it directly in the CSV value — Google strips the tick from display but preserves the text type.

#### Anti-patterns

| Don't do this | Do this instead |
|---------------|-----------------|
| Strip `£` signs before CSV | Leave them — Google detects UK currency |
| Format numbers as strings (`"95"`) | Plain `95` — let Google type it |
| Inline 500-row CSV as `content` | Write to deposit, use `source=` |
| Build formulae with absolute values | Use `=SUM(B2:B10)` — formulae work |
| Manually pad columns with spaces | CSV handles alignment; Sheets renders it |
| Bare commas in values (`£65,000`) | Quote: `"£65,000"` — or CSV breaks |

## Gmail Attachments

PDFs and images are extracted eagerly. **Office files (DOCX/XLSX/PPTX) are skipped** during thread fetch (5-10s each). Extract on demand:

```python
fetch("thread_id", attachment="budget.xlsx", base_path="...")
```

See `references/deposit-structure.md` for the full attachment layout.

## Workflow 5: Folder Triage

**When:** "Organise this Drive folder" / "What's in this folder?" / "Move all the spreadsheets into one place"

Three-step loop: explore → understand → batch-move.

### Step 1: Find subfolders

```python
# Find subfolders inside a parent (query optional — type alone is enough)
search(type="folder", folder_id="<parent_id>", base_path="...")

# Or search by name if you don't have the parent ID
search("Q4 reports", type="folder", base_path="...")
```

### Step 2: Explore the tree

```python
# Recursive fetch — builds full indented tree, capped at depth 5 / 1000 items
fetch("<folder_id>", recursive=True, base_path="...")
```

The deposited `content.md` shows the full hierarchy with file IDs. Read it to understand what's where.

**If the tree is truncated** (`cues["truncated"] is True`): the cap was hit. Fetch individual subfolders separately to explore those branches:

```python
fetch("<subfolder_id>", recursive=True, base_path="...")  # repeat per branch
```

### Step 3: Batch move

```python
# Move multiple files in one call — validates destination once, per-file summary
do(
    operation="move",
    file_id=["<id1>", "<id2>", "<id3>"],
    folder_id="<dest_id>",
    base_path="..."
)
# Returns: {batch: true, total: 3, succeeded: 2, failed: 1, results: [...]}
```

Check `results` in the response — each entry has its own `ok`/`error`. A failed move on one file doesn't block the others.

### Anti-patterns

| Pattern | Problem | Fix |
|---------|---------|-----|
| Assume truncated tree is complete | Miss files in capped branches | Check `cues["truncated"]` and fetch sub-branches |
| Search with full-text query for type filter | Unnecessary, adds noise | `type="folder"` alone is enough with a `folder_id` |
| Move files one at a time | Slow, no batch summary | Pass `file_id` as a list |

## Workflow 6: Inbox Triage

**When:** "Help me triage my inbox" / "Archive everything from that newsletter" / "Star the important threads"

The triage workflow combines search, review, and batch actions. Pagination and batch ops work together — search surfaces the full picture, batch operations let you act on it efficiently.

### Step 1: Search with Gmail operators

Target what matters using operators rather than keywords:

```python
# Unread in inbox
search("is:unread in:inbox", sources=["gmail"], base_path="...")

# Recent unread from a person
search("is:unread from:alice@example.com newer_than:7d", sources=["gmail"], base_path="...")

# Newsletters and promotions (good candidates for bulk archive)
search("category:promotions newer_than:30d", sources=["gmail"], base_path="...")

# Custom label
search("label:project-alpha is:unread", sources=["gmail"], base_path="...")
```

**Results are capped at `max_results` (default 20).** Search paginates internally up to that cap, then stops — a 200-thread inbox searched with defaults returns 20 threads. When the cap is hit, the response carries `cues.gmail_truncated` (not `cues.truncated`) telling you more exist. For real triage, pass `max_results` explicitly (e.g. 100+) and check for `gmail_truncated` before believing you've seen everything — a silently-partial picture is how shallow triage happens.

### Step 2: Review and decide

Read the search results. Each thread shows subject, participants, date, and snippet (drawn from the **latest** message). Three fields answer "whose move is it?" without fetching the thread: `last_sender` (who spoke last — `from` is the thread *originator*, often a different person), `from_me` (the latest voice is yours; `null` means identity unresolved — don't read it as "theirs"), and `unread_count`. Decide which threads to act on — fetch individual threads if you need more context before deciding:

```python
fetch("thread_id", base_path="...")  # Read the full conversation
```

### Step 3: Act in batch

Pass a list of thread IDs to process multiple threads in one call:

```python
# Archive threads you've reviewed
do(operation="archive", file_id=["thread1", "thread2", "thread3"], base_path="...")

# Star threads that need follow-up
do(operation="star", file_id=["thread4", "thread5"], base_path="...")

# Label threads for a project
do(operation="label", file_id=["thread6", "thread7"], label="follow-up", base_path="...")
```

Each batch call returns a summary with `succeeded`/`failed` counts and per-thread results — a failed operation on one thread doesn't block the others.

### The `label` operation covers more than labels

`label` works with system labels, which means it handles several triage actions through one operation:

| Triage action | How |
|---------------|-----|
| Archive | `archive` (or `label` with `label="INBOX"`, `remove=True`) |
| Star | `star` (or `label` with `label="STARRED"`) |
| Unstar | `label` with `label="STARRED"`, `remove=True` |
| Mark read | `label` with `label="UNREAD"`, `remove=True` |
| Mark unread | `label` with `label="UNREAD"` |
| Add custom label | `label` with `label="your-label-name"` |
| Remove custom label | `label` with `label="your-label-name"`, `remove=True` |

Label names are resolved automatically — use human-readable names like `"follow-up"`, not Gmail's internal IDs.

### Drafting emails

Compose drafts for the user to review and send from Gmail:

```python
# New email
do(operation="draft", to="alice@example.com", subject="Q4 update", content="...", base_path="...")

# Reply in a thread
do(operation="reply_draft", file_id="thread_id", content="Thanks for the update...", base_path="...")

# Include Drive files as formatted links in the body
do(operation="draft", to="team@example.com", subject="Report ready",
   content="Here's the report", include=["drive_file_id"], base_path="...")
```

Draft-only — Claude composes, the user reviews and sends from Gmail. This is a safety boundary, not a limitation.

**`respond` is the one op in this family that ACTS rather than drafts:** the RSVP registers on the live event immediately and the organiser sees it, exactly as if clicked in Calendar. Use it on an explicit ask ("accept it", "decline that meeting") — never speculatively during triage. `file_id` takes the invite's thread ID (resolved to the event via its iCalUID, disclosed in cues) or the event ID; it refuses cancelled meetings and events the user isn't an attendee of.

**What recipients see from `include=`** (characterised live, 2026-08-09): each file renders as a **Gmail Drive chip** — the grey rounded card with the file-type icon, same as the composer's own "insert from Drive" — because mise emits Gmail's own chip markup (plain styled HTML; Gmail never upgrades bare links to chips at read time, so markup at compose time is the only route). The text/plain part carries emoji + URL lines for non-HTML clients. **One thing the native composer does that mise does not: check the recipient can access the file.** Gmail's compose UI offers "Share & send"; mise sends the chip regardless, and a recipient without access hits "request access" on click. Before including a file someone outside the owner's domain needs, share it first (`do(share)`) — mise won't warn you.

Both draft ops auto-append the user's Gmail signature (from their sendAs settings, links intact) to the body. **Don't write a sign-off in `content`** — no "Best regards, ..." — end at the last sentence; the real signature lands below it. The `signature` cue in the response confirms it was appended.

**One draft per thread.** Gmail's conversation view shows only ONE draft inline per thread — a second draft object exists but hides exactly where the user hits Send. So `reply_draft` refuses when the thread already carries a draft, naming its id. The right move is almost always to **update the existing draft**: `do(operation="draft", file_id="<draft_id>", content=...)`. Pass `supersede=True` only when you deliberately want to discard the old draft and start fresh (permanent — and it may eat the user's hand-edits, so check whose words are in it first: the refusal includes a snippet).

### Common mistakes

| Mistake | What happens | Better approach |
|---------|-------------|-----------------|
| Keyword soup for triage search | Noisy results, hard to batch-act | Use operators: `is:unread in:inbox newer_than:7d` |
| Archiving without reviewing | Important threads disappear | Fetch uncertain threads first, then batch the clear ones |
| One thread at a time | Slow, many tool calls | Pass `file_id` as a list for batch operations |
| Separate mark_read operation | Doesn't exist as its own op | Use `label` with `label="UNREAD"`, `remove=True` |
| Forgetting `sources=["gmail"]` | Searches Drive too, slower and noisier | Set `sources=["gmail"]` for inbox work |

## Error Handling

| Error | Meaning | What to do |
|-------|---------|------------|
| `AUTH_EXPIRED` | OAuth token stale | Call `mise.do(operation="setup_oauth")` to re-authenticate (see First Run above) |
| `NOT_FOUND` | File/thread doesn't exist | Verify the ID; file may have been deleted or moved |
| `PERMISSION_DENIED` | No access to resource | Tell user they need to request access |
| `RATE_LIMITED` | Hit API quota | Wait 30s and retry once |
| `EXTRACTION_FAILED` | Couldn't parse content | Report to user with the file type and error detail |

## Anti-Patterns

| Pattern | Problem | Fix |
|---------|---------|-----|
| Keyword soup in Gmail | Noisy, imprecise results | Use `from:`, `filename:`, `after:` operators |
| Gmail operators in Drive search | 400 error from API | Drive uses plain keywords, not `from:`/`is:` |
| `from:X` for "what did X share" | Misses threads where X is recipient | `(from:X OR to:X OR cc:X)` |
| Trusting short tokens (`PR`, `AI`) | API stricter than UI — false "not found" | Participants + date filters; confirm in Gmail UI |
| Skip comments.md | Miss the real discussion | Check after every doc/sheet/slides fetch |
| Ignore email_context | Miss the story behind the file | Follow the exploration loop |
| Reading the raw search JSON top to bottom | Token waste on 35 results | `jq` the fields you need — filter it, don't skip it |
| Treating `preview` as the result set | Silently misses everything past rank 5 — the source of false "doesn't exist" claims | `cues.preview_partial` says when there's more; `jq` the deposit |
| Declaring something absent from a preview | An unseen rank 8 reads exactly like a null | `jq -r '.drive_results[] \| .name'` before any "not found" |
| Declaring something absent while `drive_truncated` is set | You searched a ceiling, not the estate | Raise `max_results` or narrow the query, then look again |
| Adding words to a search that found nothing | Drive full-text is AND — more words match *fewer* files, often zero | Drop terms, not add them; try the house noun |
| Stop after first search | Shallow understanding | Loop: new terms → new searches |
| Omit base_path | Deposits vanish into server directory | Always pass it |
| Overwrite a doc with images/tables | Content destroyed, not recoverable | Use `prepend`/`append`/`replace_text` |
| `replace_text` without checking cues | No longer silent, but still a no-op | Read `cues.warning` for `NO CHANGE`, or `cues.occurrences_changed > 0` |
| A find string copied from `content.md` | `**`, `` ` ``, `~~`, `{++` are rendering, not document text — can never match | Search the plain words; the `NO CHANGE` warning names the marker |
| Share with `confirm=True` without preview | Bypasses user approval | Always call without confirm first, show preview, then confirm |
| Archive/star one thread at a time | Slow — one tool call per thread | Pass `file_id` as a list for batch operations |
| Looking for a `mark_read` operation | Doesn't exist | Use `label` with `label="UNREAD"`, `remove=True` |

## Integration

**Composes with:**
- **todoist-gtd** — @Claude inbox items may request research; results inform outcomes

## When to Use

- Research tasks involving multiple Drive/Gmail sources
- Finding context around a document (who sent it, what was discussed)
- Creating or editing Google Docs/Sheets/Slides
- Inbox triage — searching, reviewing, and batch-acting on Gmail threads
- Composing email drafts (new or reply)
- Any task needing cross-source exploration

## Boundaries

- Task doesn't involve Google Workspace (no Drive or Gmail)
- Pure filesystem operations

## Success Criteria

This skill works when:
- Gmail searches use operators, not keyword soup
- Drive searches use keywords, not Gmail operators
- `comments.md` is checked after every doc/sheet/slides fetch
- `email_context` hints are followed to source emails
- Large results are filtered before reading — with `jq` against the deposit, not by reading `preview` and stopping
- `drive_count` / `gmail_count` are checked before any "I couldn't find it" is reported to the user
- Research tasks follow the exploration loop, not single-search-and-stop
- Triage uses batch operations, not one-thread-at-a-time
- `label` is used for mark_read/unread/unstar rather than seeking separate operations
- Errors are reported with actionable guidance, not just "it failed"
