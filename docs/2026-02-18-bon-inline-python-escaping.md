# Field Report: Inline Python escaping failures in bon piping

**Date:** 2026-02-18
**Context:** /close session, piping `bon show --json` through `python3 -c`

## What happened

Multiple attempts to pipe `bon show --json` output through inline `python3 -c` scripts failed with `SyntaxError: unexpected character after line continuation character`. The `!=` operator was being escaped as `\!=` by shell interpolation.

## Root cause

Bash heredoc-style string handling inside `python3 -c "..."` with double quotes. The shell interprets `!` as history expansion even inside double quotes in some contexts. When Claude writes `!=` inside a `python3 -c "..."` call, some shells expand or escape it unpredictably.

## Fix

Use single-quoted strings for `python3 -c '...'` (no shell interpolation), or write to a temp .py file and execute it. Avoid double-quoted inline Python with operators that contain `!`.

## Where this matters

The bon skill suggests piping `bon show --json` through `python3 -c` for parsing. Any Claude doing this with `!=` comparisons in the inline script will hit this. Not a bon bug — a shell escaping hazard that's easy to hit.

## Recommendation

When writing inline `python3 -c` commands, always use single quotes for the outer delimiter. Reserve double quotes for Python string literals inside.
