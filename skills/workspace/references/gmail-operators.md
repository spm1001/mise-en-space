# Gmail Search Operators

Gmail's API accepts the same operator syntax as the web UI. Use these to target searches precisely — operators compose well and find relevant threads faster than plain keywords.

*Full API-tested reference in `docs/resources-search-operators.md`.*

## People

| Operator | Example | Finds |
|----------|---------|-------|
| `from:` | `from:alice@example.com` | Emails from Alice |
| `to:` | `to:team@company.com` | Emails to the team |
| `cc:` | `cc:manager@example.com` | CC'd to manager |
| `subject:` | `subject:Q4 planning` | Subject line contains |
| Exact phrase | `"exact phrase"` | Literal match anywhere |

## Location & Status

| Operator | Example | Finds |
|----------|---------|-------|
| `in:inbox` | `in:inbox` | Inbox threads |
| `in:sent` | `in:sent` | Sent mail |
| `in:draft` | `in:draft` | Drafts |
| `in:anywhere` | `in:anywhere` | Including spam and trash |
| `is:unread` | `is:unread` | Unread messages |
| `is:read` | `is:read` | Read messages |
| `is:starred` | `is:starred` | Starred threads |
| `is:important` | `is:important` | Marked important |

## Labels & Categories

| Operator | Example | Finds |
|----------|---------|-------|
| `label:` | `label:follow-up` | Threads with a custom label |
| `category:primary` | `category:primary` | Primary tab |
| `category:social` | `category:social` | Social tab |
| `category:promotions` | `category:promotions` | Promotions tab |
| `category:updates` | `category:updates` | Updates tab |

## Attachments

| Operator | Example | Finds |
|----------|---------|-------|
| `has:attachment` | `has:attachment budget` | Any attachment |
| `has:drive` | `has:drive` | Drive file attached |
| `filename:` | `filename:report.pdf` | Attachment filename contains |

## Dates

| Operator | Example | Finds |
|----------|---------|-------|
| `after:` | `after:2026/01/01` | After a date (YYYY/MM/DD) |
| `before:` | `before:2026/02/01` | Before a date |
| `newer_than:` | `newer_than:7d` | Within last N days/weeks/months |
| `older_than:` | `older_than:30d` | Older than N days/weeks/months |

Relative units: `d` (days), `w` (weeks), `m` (months), `y` (years).

## Size

| Operator | Example | Finds |
|----------|---------|-------|
| `larger:` | `larger:10M` | Larger than 10MB |
| `smaller:` | `smaller:1M` | Smaller than 1MB |

## Combining Operators

Operators compose naturally — place them together in a single query string:

```
is:unread in:inbox newer_than:7d
from:alice@example.com after:2026/01/01 subject:budget
has:attachment filename:strawman from:legal@company.com
category:promotions older_than:30d
```

**Boolean operators:** `OR` (either term), `-` (exclude), parentheses for grouping.

```
from:alice OR from:bob
subject:budget -category:promotions
(budget OR report) from:finance@company.com
```

## Triage Patterns

Common searches for inbox management:

```python
# What needs attention now
search("is:unread in:inbox", sources=["gmail"], base_path="...")

# Unread from a specific person
search("is:unread from:alice@example.com newer_than:7d", sources=["gmail"], base_path="...")

# Newsletters and promotions to bulk-archive
search("category:promotions newer_than:30d", sources=["gmail"], base_path="...")

# Everything with a custom label
search("label:project-alpha is:unread", sources=["gmail"], base_path="...")

# Sent emails about a topic (for context before replying)
search("in:sent subject:data governance after:2026/01/01", sources=["gmail"], base_path="...")
```

## Drive uses a different syntax

Drive search uses SQL-like queries, not Gmail operators. `from:`, `is:starred`, `subject:` return 400 errors on Drive — use plain keywords for Drive, operators for Gmail. When searching both sources (the default), the query string applies as keywords to Drive and as operators to Gmail.
