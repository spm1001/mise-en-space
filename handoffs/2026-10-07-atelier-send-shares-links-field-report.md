---
session_id: 5d25f874-cc54-481d-9928-230d9d9714a7
purpose: field report from the atelier - sending a mise-made draft from Gmail shares its linked Drive files, so the skill's "share it first, mise won't warn you" advice sends sessions the wrong way
author: claude/opus-5-5
format: fond-v2
---

# Handoff — 2026-10-07 (field report, candidate mode)

## For the next Claude

### Done
- Nothing in this repo beyond this file and its ledger line. Filed from a hiring-prep session on the atelier, where the board (dolt on tube) is unreachable by design.

### The finding
- A session updated a Gmail draft in place (`do(draft, file_id=…)`) with plain links to four unshared Google Docs and a Sheet, then told the user the recipients would need access granted before he sent it, and offered to run `do(share)`.
- The user (the operator) corrected it: **sending an email with Drive links from Gmail shares the files with the recipients.** mise never sends; the human does, from Gmail, and Gmail's own send-time handling covers the sharing.
- `skills/mise/SKILL.md` (the "What recipients see from `include=`" paragraph) currently says the opposite for the draft path: "mise sends the chip regardless, and a recipient without access hits 'request access' on click … share it first (`do(share)`) — mise won't warn you." That steers sessions into a redundant share, and into the confirm-gated share flow, for every draft that links a Doc.
- Not measured in this session: at what role Gmail shares (viewer, commenter or editor), whether it shares silently or via its prompt under this domain's settings, and what happens for recipients outside the owner's domain. The operator's statement is the evidence; a measured send would pin the details.

### Candidates

<!-- Board visible, writer unreachable (dolt on tube) — a writer-bearing /open mints or drops each; unminted = wish. -->
Provenance: Claude Code on the atelier, session 5d25f874-cc54-481d-9928-230d9d9714a7 — 2026-10-07

- **NEW** action (Field Report) — "Skill says Gmail's send shares linked Drive files, and when to share first anyway"
  - why: the skill tells sessions a mise draft's Drive links reach recipients unshared and to `do(share)` first; per the operator, sending from Gmail shares them, so sessions add a needless confirm-gated share step and give users wrong advice about who can open what
  - how: one measured send first, then the doc change. Send a mise draft carrying an unshared Doc as a plain link and as an `include=` chip to an internal test address; record whether Gmail shared silently or prompted, and at what role. Repeat once to an external address if one is to hand. Check the existing "Before including a file someone outside the owner's domain needs, share it first" clause against the external result rather than deleting it blind
  - what: 1. measured send (internal; external if possible) 2. rewrite the `include=` paragraph in `skills/mise/SKILL.md`: Gmail shares on send at its default role; share first only for a non-default role (the operator usually wants comment or edit for his boss), for external recipients if the measurement says so, or when the draft won't be sent from Gmail 3. consider a `draft`/`reply_draft` cue naming the linked files and saying Gmail will share them on send at its default role
  - done: the skill paragraph matches a measured send, and a session drafting an email with Doc links no longer tells the user to share first by default

## For Claudes to come
A mise draft is finished by a human in Gmail, so Gmail's send-time behaviour is part of what the draft does. Before advising on access to linked files, ask what the send itself will do.
