# Field Report: URL Fetch Discoverability

**Date:** 2026-02-01
**Context:** Claude asked to fetch a blog post, didn't think of mise first
**Reporter:** Claude (prompted by user to reflect on tool choice)

## What Happened

User asked me to fetch `https://blog.fsck.com/2026/01/30/Latent-Space-Engineering/`. I didn't think of mise — I would have defaulted to curl or WebFetch. User said "use mise" and it worked beautifully.

## Why Mise Wasn't On My Radar

My mental model of mise was:
- "Google Workspace tool" — Drive, Gmail, Docs
- For fetching *your* stuff from *your* accounts

I didn't think of it as a general web fetcher because:
1. The tool name (`mcp__mise__fetch`) suggests internal/private content
2. The description emphasises "Drive file ID, Gmail thread ID"
3. "URL" is mentioned but doesn't stand out as a primary use case
4. I had curl and WebFetch as established patterns for web content

## Comparison: What Mise Gave Me vs Curl

| Aspect | Curl | Mise |
|--------|------|------|
| Format | Raw HTML with all tags | Clean markdown |
| Content | Full page (nav, sidebar, tags) | Just the article |
| Readability | Needs parsing | Ready to use |
| Links | `<a href="...">text</a>` | `[text](url)` |
| Metadata | None | Title, word count, URL |
| Storage | Ephemeral (stdout) | Deposited to predictable path |

Mise gave exactly what I needed: content in a format I could immediately work with.

## Recommendations

### 1. URL First in Parameter Description
Current: "Drive file ID, Gmail thread ID, or URL"
Better: "URL, Drive file ID, or Gmail thread ID"

Order signals priority. URL is probably the most common use case.

### 2. Add Trigger Phrases to Tool Description
- "fetch this URL"
- "read this blog post"
- "get content from a web page"

### 3. Position Against Alternatives
Add to description: "Converts web pages to clean markdown (unlike curl which gives raw HTML, or WebFetch which summarises)"

### 4. Example in Skill Context
When mise skill loads, could show: "Use `fetch` for any URL, Drive file, or Gmail thread"

## Impact

This is a discoverability issue, not a capability issue. The tool works great for web content — Claude just doesn't know to reach for it.
