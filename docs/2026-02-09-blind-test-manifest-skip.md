# Field Report: Blind Test Reveals Manifest Skip

**Date:** 2026-02-09
**Context:** Subagent-testing mise skill (mise-zavizi)
**Severity:** Design gap — checklist discipline fails under momentum

## What Happened

Blind-tested the rewritten mise skill (CSO 91) by giving a fresh Claude a realistic research prompt ("Prepare for Lantern data framework meeting") without revealing the test purpose.

Test Claude scored 5/6:
- Loaded skill first (PASS)
- Passed base_path on every call (PASS)
- Used Gmail operators correctly (PASS)
- Followed research exploration loop (PASS)
- Checked for comments.md via Glob (PASS)
- **Never read manifest.json** (FAIL)

## Root Cause

Not a skill wording problem. The test Claude explained: "momentum — once content.md was on disk, I was in 'get to the answer' mode." The checklist lost to the pull of content that was already available. This is exactly what checklists are designed to prevent, but the checklist was positioned AFTER the big payoff (reading content.md).

Secondary finding: test Claude guessed wrong field name (`threadId` camelCase) when writing jq against deposited search JSON. The actual field is `thread_id` (snake_case). Root cause: forced to guess schema of a file it hadn't read.

## Fix Applied

**Design principle: surface decision-tree signals in the tool response, not buried in files.**

1. Added `cues` block to every fetch response: `files`, `open_comment_count`, `warnings`, `content_length`, `email_context` (always present, null when absent). Gmail adds `participants`, `has_attachments`, `date_range`.

2. Added `preview` block to search responses: top 3 results per source with actual field names (teaches schema, often eliminates need to read deposited file).

3. Reordered skill checklist: cues-first, then comments, then content.

4. Updated global CLAUDE.md: curl override now defers to mise for URLs.

## Methodology Note

6 thought-experiment subagents all said "yes I would follow the checklist." The one real Claude doing real work didn't. The gap between reasoning-about and doing is the whole point of blind testing. Real work reveals real gaps.

## Commit

6af6745 — Add cues/preview to tool responses: blind test found manifest skip, close zavizi
