# Results — mise-tijeko caller expectations

Pre-registration: `predictions.md`, committed at `dc4dfbf` (22:22:45 BST, 2026-09-22). The eight subjects ran 22:22–22:23, after that commit. One correction to the pre-registration's own text: it says it was written "~22:50 BST". That time was my guess and it was wrong by half an hour. The commit time is the real one, and it still comes before every run.

Raw answers are in `runs/`, and the scoring against the fixed rule is `scored.json` (`uv run --script score.py`). Every subject's `modelUsage` names the model it was asked for: three `claude-opus-5-5`, three `claude-fable-5-1`, two `claude-sonnet-5`. Total cost was $1.72 on the Vertex pot: Fable about $0.38 a subject, Opus $0.14, Sonnet $0.08.

## What the room said

| Case | Today | predict | prefer | distrust | Decided | Shipped |
|---|---|---|---|---|---|---|
| C1 reply_all + cc | replace | union 7, replace 1 | **union 8** | replace 8 | union (rule 1) | cc now adds to the reply-all Cc |
| C2 two folder ids | first_wins | first_wins 6, second_wins 2 | **refuse 8** | first_wins 8, second_wins 8 | refuse | different ids refuse, nothing moves |
| C3 folder + content | ignore | ignore 8 | ignore_warn 5, refuse 3 | consume 7, ignore 4, refuse 1 | ignore_warn (rule 1, exactly 5) | folder made, warning names the ignored params and the folder_id to create a doc into |
| C4 page_setup on form | ignore | ignore 8 | **ignore_warn 8** | refuse 4 | ignore_warn | warning, on forms and folders |
| C5 form from file_path | refuse_generic | consume 4, refuse_generic 4 | **consume 8** | refuse_generic 6 | consume | file_path read as the spec; source= on a form (create or overwrite) now refuses with teaching |
| C6 form into folder | root_warn | consume 5, root_silent 2, root_warn 1 | **consume 8** | root_silent 8 | consume | Drive re-parents the form after minting; the folder is checked first |
| C7 restore_comment on Sheet | ignore | ignore 8 | ignore 5, ignore_note 3 | refuse 8 | ignore | recorded tolerance |
| C8 confirm, no attendees | ignore | ignore 8 | ignore 6, ignore_note 2 | refuse 8 | ignore | recorded tolerance |
| C9 meet=False on update | partial_silent | partial_silent 8 | **consume 8** | partial_silent 8 | consume | meet's default is now None; False removes the link, gated as a structural change |

Opus 5.5 and Fable 5.1 had the same favourite answer in all nine cases. Sonnet 5 split off on only two, C7 and C8, where both Sonnet subjects wanted a note while the other six wanted silence.

The best single sentence came from a Fable subject on C9: *"if that cannot be done, the result must say so or I will report a removal that never happened."* That is exactly what happened in the one real C1 incident, below.

## The column worth reading: predict against today

"Perturbing" in the card's sense means callers believe the tool did one thing when it did another. That's the gap between **predict** and **today**, and it splits the cases in two:

- **Callers expected a silent drop, and they got one:** C3, C4, C7, C8 and C9, where every one of the eight predicted today's behaviour. They didn't like it in C3, C4 and C9, but they weren't fooled. A caller who expects a drop checks for one.
- **Callers expected the good behaviour, and got the drop:** C1, where 7 of 8 predicted union and the tool replaced, and C6, where 5 of 8 predicted consume and the tool went to root. These are the dangerous ones, because the caller walks away believing the wrong thing. C1 is also the case with a real incident behind it.

So by the card's own failure mode ("Claudes perturbed by our decision in use, mistrusting the tool"), C1 was the worst drop in mise by a distance, and the transcripts agree.

## What the call log couldn't see and the transcripts could

The pre-registration counted zero real calls for C1–C6 in `~/.local/share/mise/calls.jsonl`. That was true, but only of the log's window, which starts on 2026-07-04. The session transcripts go back to 2026-02-15. I swept them for the same shapes, deduplicated on `tool_use.id` because `~/.claude-commis/projects` mirrors `~/.claude/projects` and a first pass double-counted everything. That gave 1,668 unique `do()` calls, with controls firing on the same pass: 90 reply_all, 62 folder creates, 22 form creates.

- **C1, 2026-03-25:** a reply-all to an external partner's thread that added one colleague on Cc. The tool drafted it with that colleague as the only Cc, and the session then told Sameer the draft replied-all to the thread — naming the seven people it said were on it — and added the colleague on Cc. The `cues.cc` field showed the single address the whole time. Nobody read it. That is the card's `--badly` happening in the wild, five months before the card was filed.
- **C5, 2026-05-11:** a feedback form created from `file_path=`. It got "requires content", then `cat` of the file, then a retry with content, so one wasted round. The spec file's own header comment said `mise do create doc_type=form content=<this file>`, which shows the author half-knew the trap.
- **Echoing defaults:** across all 1,668 calls, an explicit `False` shows up only on `restore_comment`, 42 times. Its default is True, so False there is a real request. No caller ever passed a boolean at its default value, and `meet` shows up absent 29 times, True 17, False 0. That removes the one worry I had about making C9 consume: that agents who habitually pass `meet=False` would start stripping links. The subjects' self-report agrees with this: 7 said they leave defaults out and 1 said "depends".

## Live probes (rule 4, feasibility)

`probe_live.py` ran on the authed ITV account using scratch artefacts only. Nothing had attendees, sendUpdates was none, and everything was permanently deleted in a `finally` block. `probe_live.run*.json` hold the evidence.

- **C6 works.** The Forms API minted into My Drive root (`0AKk…`), and `_move_file` re-parented the form into a fresh folder. Reading it back showed the folder as the only parent.
- **C9 works, on the second try.** Patching `conferenceData: null` (with `conferenceDataVersion=1`, which `patch_event` always sends) left no `conferenceData` and no `hangoutLink` in either the patch response or a fresh read. Run 1 got `403 rateLimitExceeded` when it patched within a second of minting the Meet conference, even after the retry decorator's three attempts. Run 2 waited 20 s and was clean. **Watch:** a real `update_event` acts on events that already exist, so it shouldn't meet this. But if a caller creates and then immediately de-Meets an event, the 403 will say "rate limit", and the actual cause is that the conference is still being provisioned. That's inferred, not measured.

## Mutation controls

Everything was staged first. Then each fix was reverted in turn, the tests I expected to fail were named in advance, and the file was restored from the index (`/tmp/tijeko_controls.py`, not committed). Five of seven went red exactly where predicted. Two went red more widely than I predicted, and both are fine:

- Removing the form's folder move also turned `test_create_form_failed_move_says_where_the_form_is` red. That test needs the move call to exist before it can fail it, so it depends on the same line.
- Flipping `DO_PARAM_DEFAULTS["meet"]` back to False turned 41 tests red, not the 2 I named. `server.do` now sends `meet=None`, so a default of False made the gate think every caller had supplied `meet`, and it refused nearly every op. The signature-mirror test (`test_do_defaults_mirror_servers_signature`) was already in place to catch exactly this drift, and it did. The wide red shows how much depends on those two defaults agreeing.

After the essayeur's fix, the full documented suite ran at 3,062 passed (107 deselected), including the consumer-doors wheel test, and `scripts/smoke_stdio.py` passed 10/10 through the real stdio envelope.

## Cold eyes

An essayeur (a verifier subagent) read the staged diff before the push and **refuted C1 as first built**. The merge fed the inferred and explicit Cc lists to one `getaddresses()` call. On Pythons that carry the CVE-2023-27043 fix (this repo's 3.11.14 does), one malformed element makes `getaddresses` return `[('', '')]` for the *whole* list. So `cc="colleague@example.com,"` — a single trailing comma — would have produced a draft with no Cc at all. That is the 25 March incident again by a new route, and worse, because the old code at least kept the explicit cc. The fix now parses the two halves separately. The inferred half is already well-formed, because `_parse_address_list` built each entry, so it is kept word for word. The explicit half has trailing separators and semicolons normalised, and anything that still won't parse (for example `cc="a@x.com b@x.com"`) refuses before any API call. A mutation control that removes the normalisation turns exactly one test red. The essayeur upheld the other three axes: every `meet` consumer, the sweep's scope (it found no missed drop), and the tests. It also left two watch items. First, `page_setup='landscape'` on a form now warns "Docs-only" instead of refusing the unsupported value, because the form early-return comes before that check; this was folded into mise-dofonu. Second, an explicit `cc` naming yourself or the To address isn't deduped against them. That behaviour is older than this change and out of scope.

## Prediction scorecard

| | Prediction | Outcome |
|---|---|---|
| P1 | union ≥ 6/8, replace distrusted ≥ 5/8 | held: 8/8 and 8/8 |
| P2 | refuse ≥ 6/8 | held: 8/8 |
| P3 | no option reaches 5/8; ignore_warn first, refuse second | **failed on the threshold**: ignore_warn got exactly 5. The order held |
| P4 | ignore_warn plurality | held: 8/8. Separately, 4/8 distrust *refusing* page_setup, which the tool does today on sheet/file (filed) |
| P5 | consume ≥ 6/8 | held: 8/8 |
| P6 | consume ≥ 6/8; root_warn distrusted ≤ 2/8 | held: 8/8 and 0/8 |
| P7 | refuse ≤ 1/8; ignore + note ≥ 7/8; **note the plurality** | first two held. **Plurality failed**: silence 5, note 3 |
| P8 | same shape as P7 | same: **plurality failed**, silence 6, note 2 |
| P9 | consume ≥ 4/8; most say omit/depends | held: 8/8 and 8/8 |
| PA | Opus and Fable agree on ≥ 7/9 | held: 9/9 |
| PB | my "want" matches the decision on ≥ 7/9 | held at the threshold: 7/9 (missed C3, C9) |
| PC | silence decided only in C7, C8 | held |

## What surprised me about my own expectations

I pre-registered as a caller, and I missed twice. Both misses happened because I answered as a maintainer instead.

- **C9.** My caller instinct said consume. I overrode it with a worry about *other* agents echoing `meet=False` as a default, and wrote partial_warn instead. The worry was a guess about behaviour I hadn't looked at. The transcripts (0 echoes in 1,668 calls) and the room (8/8 consume) both said my first instinct was right.
- **C3.** I wanted a refusal because house doctrine says teaching refusals get obeyed in seconds. The room wanted the folder plus a warning, because the folder is plainly what was asked for. The doctrine holds for real mistakes. The room treated content-on-a-folder as a slip in a request that was otherwise clear, and that deserves a note, not a stop.
- **P7/P8.** I predicted Claudes would want a note more than I do for flags whose intent is already met. The Opus and Fable subjects wanted silence even more than I did. Only Sonnet wanted to be told. My model of "Claudes like being told things" was really a model of one family.

The general lesson: I predicted today's behaviour no better than the room did. Where I lost, I had stopped reading as a caller and started reading as a maintainer. That's the card's premise working, measured on its author.

## Limitations

The answers are stated preferences from one turn with the options listed, not behaviour observed in the middle of a task. All nine cases shared one prompt. The tiebreaker never fired: every case was decided by rule 1, although C3 and C7 cleared it at exactly 5. n = 8 across three families. The transcript sweep covers tube's `~/.claude` and `~/.claude-commis` only, with no Mac and no Cowork. Its C1 and C5 hits are single incidents, not rates.
