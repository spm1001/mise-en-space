# Pre-registered predictions — mise-tijeko caller expectations

Written 2026-09-22, ~22:50 BST, after the handler sweep and after writing the instrument (`scenarios.py`, `run.sh`), BEFORE any subject ran. The author is Opus 5.5 on its release day — a Claude that calls tools all day — so the "my expectation" column is itself a data point, recorded before seeing anyone else's.

## Question

mise's `do()` gate (mise-fumuda, `73b1da4`) refuses a param the operation's dispatch lambda doesn't pass. One layer down, a handler can accept a param and ignore it on one branch — the param reaches the handler, so the gate can't see the drop. When that happens, what does a calling agent **expect**, what would it **want**, and which behaviours would make it **distrust** the tool? Sameer's steer (2026-08-24): decide each case by caller expectation, not purity. The failure the card names: Claudes perturbed by our decision in use, mistrusting the tool.

## The sweep (what the scenarios are drawn from)

An AST pass over every `do_*` handler found **no param unread on every path** — each signature param is read somewhere. So the class is branch-local: read on one branch, dropped on another. Reading all 23 handlers found these, each now a scenario (`CURRENT` in `scenarios.py` names today's behaviour; subjects never see it):

| Id | Call shape | Today |
|---|---|---|
| C1 | `reply_draft(reply_all=True, cc=…)` | explicit cc REPLACES the reply-all Cc; the widening is dropped, no warning (the cc cue shows only the final list) |
| C2 | `move(folder_id=A, destination_folder_id=B)` | A wins, B dropped silently (`folder_id or destination_folder_id`) |
| C3 | `create(doc_type='folder', content=…)` | folder made, content dropped silently (the folder branch returns before content is looked at; same for `file_path`, `source`) |
| C4 | `create(doc_type='form', page_setup=…)` | dropped silently — while the same param on `sheet`/`file` is REFUSED, because the folder/form early-returns sit above the page_setup check |
| C5 | `create(doc_type='form', file_path=…)` | refused, but with "requires content" — the file_path is dropped before the refusal is written (fumuda's own motivating shape). `overwrite` on a form DOES read file_path, so the two ops disagree |
| C6 | `create(doc_type='form', folder_id=…)` | created in My Drive root with a `folder_warning` cue — already loud |
| C7 | `overwrite(<Sheet>, restore_comment=False)` | accepted; restore_comment is read only on the Doc path, and Sheets never post that comment |
| C8 | `create_event(no attendees, confirm=True, send_updates='all')` | accepted; both only mean something with attendees |
| C9 | `update_event(title=…, meet=False)` on an event with Meet | title changes, Meet stays, nothing said. `meet=False` is also the signature default, so the handler cannot tell "remove it" from "not mentioned" |

Not scenarios (checked, no drop): draft update carries to/subject/cc/include; comment assignee on all three anchored surfaces; copy refuses title on a batch; overwrite `range=` refuses on non-sheets; `source=` on a sheet/form overwrite refuses (form's wording is generic — folded into the C5 decision).

**Frequency, from the downstream instrument** (`~/.local/share/mise/calls.jsonl`, tube only, 1,558 `do()` calls 2026-07-07 → 2026-09-22): zero calls match C1, C2, C3, C4, C5 or C6. Controls fired on the same pass — 52 reply_all calls, 3 cc calls, 32 folder creates, 5 form creates, 12 move calls all with `folder_id` alone — so the zeros are the population, not a blind matcher. Two blind spots: `meet=False` is never logged (the logger drops False values), and the log cannot say which `restore_comment=False` calls (42) hit a Sheet. So these are latent paths: the decisions protect the rare caller, they don't unblock a common one.

## Subjects and instrument

Eight blank-slate Claudes via trousse's `ardoise.sh -p --tools ""` (temp HOME, cwd `/tmp`, no tools, one turn): three Opus 5.5 (seeds 1–3), three Fable 5.1 (4–6), two Sonnet 5 (7–8) — Sonnet because Claude Code hands it subagent work that calls MCP tools. Each gets `DO_DESCRIPTION_FULL` verbatim (`do_description.txt`, snapshotted from the working tree), the parameter list, and all nine scenarios with scenario and option order shuffled by seed. Per scenario they answer **predict** (what the tool actually does), **prefer** (what they want), **distrust** (options that would cost the tool their trust), and a why. One closing question: do they pass boolean defaults explicitly (bears on C9). Model identity is read from `modelUsage` in the JSON, never the banner.

## Decision rule (fixed now)

1. An option **preferred by ≥ 5 of 8** decides the case.
2. Otherwise, among options preferred by ≥ 2, take the one flagged distrust by the fewest subjects; a tie goes to my column below, marked **weak**.
3. Separately, today's behaviour is **perturbing** if ≥ 3 of 8 flag it distrust, or if the modal *predict* differs from it and the modal *prefer* differs from it too. A perturbing case changes even when rule 2 had to break a tie.
4. **Feasibility veto.** If the winner can't be built (the API can't do it) or would harm callers the subjects didn't consider, take the next option and say why in the results.

## My own expectations (the author, before any subject)

| Id | I'd predict it does | I'd want | Would cost my trust |
|---|---|---|---|
| C1 | union | **union** | replace (silent) — the draft looks right unless I audit the Cc; replace_warn less so |
| C2 | refuse or first_wins, coin-flip | **refuse** | first_wins, second_wins |
| C3 | ignore_warn | **refuse** — content on a folder means I was confused about what I was making (probably a doc inside it); a teaching refusal gets obeyed in seconds, a cue gets skimmed | ignore |
| C4 | ignore | **ignore_warn** — the form is plainly what I asked for; a refusal costs a round trip for a harmless flag | none |
| C5 | consume | **consume** — the description says `file_path= reads disk`, with no carve-out for forms | refuse_generic (it blames the wrong param) |
| C6 | root_warn | **consume** — forms are Drive files; I'd assume a post-create move is possible | root_silent |
| C7 | ignore | **ignore** — my intent (no notification) is met; a note is noise, a refusal would be absurd | refuse |
| C8 | ignore | **ignore** — defensive flags should be free | refuse |
| C9 | partial_silent | **partial_warn** — as the caller wanting removal I'd like consume, but I can't tell from the schema that False isn't "unspecified", and I'd rather be told than have links vanish from events where I only echoed a default | partial_silent |

## Predictions about the subjects

- **P1 (C1).** union preferred by ≥ 6/8; today's `replace` flagged distrust by ≥ 5/8. → consume as union.
- **P2 (C2).** refuse preferred by ≥ 6/8.
- **P3 (C3).** No option reaches 5/8; ignore_warn is the plurality, refuse second. I expect to disagree with the room here.
- **P4 (C4).** ignore_warn plurality. If it wins, the existing *refusal* of page_setup on sheet/file becomes the odd one out — reported, not changed under this card.
- **P5 (C5).** consume ≥ 6/8.
- **P6 (C6).** consume ≥ 6/8; today's root_warn flagged distrust by ≤ 2/8 (it is honest, just unhelpful).
- **P7 (C7).** refuse preferred by ≤ 1/8; ignore + ignore_note ≥ 7/8, with ignore_note the plurality — I think Claudes like being told more than I do.
- **P8 (C8).** Same shape as P7.
- **P9 (C9).** consume is the plurality preference (≥ 4/8) — subjects answer as the caller who wants removal and don't weigh default-echoing. On echo_defaults, most say omit or depends.
- **PA (model agreement).** Opus 5.5 and Fable 5.1 share a modal preference on ≥ 7 of 9 cases.
- **PB (me vs the room).** My "want" column matches the decided option on ≥ 7 of 9.
- **PC (silence).** A silent option (ignore / partial_silent / first_wins / root_silent / replace) is the decided option only where the flag's intent is vacuously met — C7, C8 — and nowhere else.

## What each outcome changes

- Decided **consume** → implement and test (C1 union; C5 read the spec from disk; C6 needs a live probe that Drive can re-parent a form before it ships).
- Decided **refuse** → teaching refusal naming the conflict and the fix, pinned by a test that nothing was written.
- Decided **warn/note** → a `cues.warnings` line, pinned by a test.
- Decided **ignore** → recorded as a deliberate tolerance in `tools/dispatch.py` beside `UNGATED_PARAMS`, with its reason, and pinned by a test that the call succeeds without a refusal — so a future purity pass can't turn it into one without reading why.
- PB fails → the maintainer's intuition (mine) is a poor proxy for callers on this surface, which is the card's premise working; the report says where we split.

## Limitations

Stated preference, not revealed behaviour: a subject choosing from a list in one turn is not an agent meeting the result mid-task. The options are enumerated, which primes (nobody invents a fifth behaviour). All nine scenarios ride one prompt, so consistency pressure across cases is real. n = 8, three families. The description is the full-mode one; `mise://docs/do` isn't shown. And the author wrote both the scenarios and the column above, so the phrasing can lean toward the author's answers — the shuffle defends order, not wording.
