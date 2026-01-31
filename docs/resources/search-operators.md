# Search Operators Reference

API-tested reference for Drive and Gmail search operators. **Key finding:** Drive uses SQL-like syntax, not Gmail-style operators — the web UI translates human-friendly operators to SQL internally, but the API only accepts SQL.

*Tested: 2026-01-31 against production APIs*

---

## Quick Summary

| API | Syntax Style | UI-Style Operators? |
|-----|-------------|---------------------|
| **Drive** | SQL-like (`fullText contains 'X'`) | ❌ No |
| **Gmail** | Gmail-style (`from:X`, `has:attachment`) | ✅ Yes |

**The mismatch matters:** Claude might guess `from:john` for Drive (fails with 400). Guide explicitly.

---

## Drive API Operators

Drive API accepts a SQL-like query language. All operators were tested 2026-01-31.

### Text Search

| Operator | Example | Notes |
|----------|---------|-------|
| `fullText contains` | `fullText contains 'budget'` | Searches file content (PDFs, Docs, etc.) |
| `name contains` | `name contains 'meeting'` | Title/filename search |

**Gotcha:** `fullText` indexes content inside PDFs and Office files, but there's no `contentSnippet` field — you can find files but not see *why* they matched.

### MIME Type Filters

| Operator | Example | Notes |
|----------|---------|-------|
| `mimeType =` | `mimeType = 'application/pdf'` | Exact match |
| `mimeType contains` | `mimeType contains 'document'` | Partial match |
| `mimeType !=` | `mimeType != 'application/vnd.google-apps.folder'` | Exclude type |

**Common MIME types:**

| Type | MIME |
|------|------|
| Google Doc | `application/vnd.google-apps.document` |
| Google Sheet | `application/vnd.google-apps.spreadsheet` |
| Google Slides | `application/vnd.google-apps.presentation` |
| Google Form | `application/vnd.google-apps.form` |
| Google Drawing | `application/vnd.google-apps.drawing` |
| Google Site | `application/vnd.google-apps.site` |
| Folder | `application/vnd.google-apps.folder` |
| PDF | `application/pdf` |
| DOCX | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| XLSX | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| PPTX | `application/vnd.openxmlformats-officedocument.presentationml.presentation` |

### Date Filters

| Operator | Example | Notes |
|----------|---------|-------|
| `modifiedTime >` | `modifiedTime > '2024-01-01'` | Modified after |
| `modifiedTime <` | `modifiedTime < '2024-01-01'` | Modified before |
| `modifiedTime >=` | `modifiedTime >= '2024-01-01'` | Modified on or after |
| `createdTime >` | `createdTime > '2024-01-01'` | Created after |

**Date formats accepted:**
- `'2024-01-01'` — Date only (midnight UTC)
- `'2024-01-01T00:00:00'` — Full ISO timestamp
- `'2024-01-01T00:00:00Z'` — With Z suffix

**NOT accepted:** Relative dates (`1d`, `1w`), Gmail-style (`after:`, `before:`)

### Ownership & Sharing

| Operator | Example | Notes |
|----------|---------|-------|
| `'me' in owners` | `'me' in owners` | Files I own |
| `'me' in writers` | `'me' in writers` | Files I can edit |
| `'me' in readers` | `'me' in readers` | Files I can view |
| `'email@example.com' in owners` | — | Specific owner |
| `sharedWithMe = true` | `sharedWithMe = true` | Shared with me (not owned) |

### Boolean Operators

| Operator | Example | Notes |
|----------|---------|-------|
| `and` | `name contains 'X' and mimeType = 'Y'` | Both conditions |
| `or` | `name contains 'X' or name contains 'Y'` | Either condition |
| `not` | `not mimeType = 'application/vnd.google-apps.folder'` | Negation |

### Other Filters

| Operator | Example | Notes |
|----------|---------|-------|
| `starred = true` | `starred = true` | Starred files |
| `trashed = true` | `trashed = true` | In trash |
| `trashed = false` | `trashed = false` | Not in trash (default) |
| `visibility = 'anyoneWithLink'` | — | Publicly shared |
| `visibility = 'limited'` | — | Private/restricted |
| `parents in 'FOLDER_ID'` | `parents in '1ABC...'` | Files in specific folder |

### ❌ OPERATORS THAT FAIL

These look like Gmail syntax but **DO NOT WORK** with Drive API:

| Fails | Use Instead |
|-------|-------------|
| `from:john` | Not available via API |
| `to:jane` | Not available via API |
| `type:document` | `mimeType = 'application/vnd.google-apps.document'` |
| `type:pdf` | `mimeType = 'application/pdf'` |
| `is:starred` | `starred = true` |
| `owner:me` | `'me' in owners` |
| `creator:me` | Not available (use owners) |
| `before:2024-01-01` | `modifiedTime < '2024-01-01'` |
| `after:2024-01-01` | `modifiedTime > '2024-01-01'` |
| `title:meeting` | `name contains 'meeting'` |
| `sharedwith:me` | `sharedWithMe = true` |
| `followup:actionitems` | UI-only feature, no API equivalent |

---

## Gmail API Operators

Gmail API accepts the same operators as the web UI. All operators tested 2026-01-31.

### Basic Search

| Operator | Example | Notes |
|----------|---------|-------|
| Simple term | `budget` | Searches everywhere |
| `from:` | `from:john@example.com` | Sender |
| `to:` | `to:team@example.com` | Recipient |
| `cc:` | `cc:manager@example.com` | CC'd |
| `bcc:` | `bcc:archive@example.com` | BCC'd (rare) |
| `subject:` | `subject:meeting` | Subject line |
| Exact phrase | `"exact phrase"` | Literal match |

### Labels & Categories

| Operator | Example | Notes |
|----------|---------|-------|
| `in:inbox` | `in:inbox` | In inbox |
| `in:sent` | `in:sent` | Sent mail |
| `in:draft` | `in:draft` | Drafts |
| `in:spam` | `in:spam` | Spam folder |
| `in:trash` | `in:trash` | Trash |
| `in:anywhere` | `in:anywhere` | Including spam/trash |
| `label:X` | `label:important` | Custom label |
| `category:primary` | `category:primary` | Tab category |
| `category:social` | `category:social` | Social tab |
| `category:promotions` | `category:promotions` | Promotions tab |
| `category:updates` | `category:updates` | Updates tab |
| `category:forums` | `category:forums` | Forums tab |

### Status Filters

| Operator | Example | Notes |
|----------|---------|-------|
| `is:unread` | `is:unread` | Unread messages |
| `is:read` | `is:read` | Read messages |
| `is:starred` | `is:starred` | Starred |
| `is:important` | `is:important` | Marked important |
| `is:snoozed` | `is:snoozed` | Snoozed |
| `is:chat` | `is:chat` | Google Chat messages |

### Attachments

| Operator | Example | Notes |
|----------|---------|-------|
| `has:attachment` | `has:attachment` | Any attachment |
| `has:drive` | `has:drive` | Drive file attached |
| `has:document` | `has:document` | Google Doc attached |
| `has:spreadsheet` | `has:spreadsheet` | Google Sheet attached |
| `has:presentation` | `has:presentation` | Google Slides attached |
| `has:youtube` | `has:youtube` | YouTube link |
| `filename:` | `filename:pdf` | Attachment filename contains |
| `filename:*.pdf` | `filename:*.pdf` | Wildcard match |

### Size Filters

| Operator | Example | Notes |
|----------|---------|-------|
| `larger:` | `larger:10M` | Larger than 10MB |
| `smaller:` | `smaller:1M` | Smaller than 1MB |
| `size:` | `size:10000` | Exactly N bytes (rare use) |

**Units:** `K` (KB), `M` (MB), `G` (GB)

### Date Filters

| Operator | Example | Notes |
|----------|---------|-------|
| `after:` | `after:2024/01/01` | After date |
| `before:` | `before:2024/12/31` | Before date |
| `older:` | `older:1d` | Older than N days/weeks/months/years |
| `newer:` | `newer:1d` | Newer than N |
| `older_than:` | `older_than:1d` | Same as older: |
| `newer_than:` | `newer_than:1d` | Same as newer: |

**Date format:** `YYYY/MM/DD` (slashes, not dashes)

**Relative units:** `d` (days), `w` (weeks), `m` (months), `y` (years)

### Boolean Operators

| Operator | Example | Notes |
|----------|---------|-------|
| `OR` | `budget OR report` | Either term |
| `-` | `-spam` | Exclude term |
| Parentheses | `(budget OR report) from:john` | Grouping |
| `AROUND N` | `AROUND 5 budget report` | Words within N of each other |

### Special Operators

| Operator | Example | Notes |
|----------|---------|-------|
| `list:` | `list:info@example.com` | Mailing list address |
| `rfc822msgid:` | `rfc822msgid:abc123` | Message ID |
| `deliveredto:` | `deliveredto:me@example.com` | Delivery address |
| `has:yellow-star` | `has:yellow-star` | Specific star type |

---

## Smart Defaults for Search Tool

Based on usage patterns (tested 2026-01-31):

### Recency Baseline (Google Docs)

| Window | Docs Found | Recommendation |
|--------|------------|----------------|
| 1 week | 56 | Too narrow for discovery |
| 1 month | 176 | Good for recent work |
| 3 months | 332 | Good default for "recent" |
| 1 year | 913 | Good for broad search |

**Suggestion:** Default to 1-year recency filter (`modifiedTime > '${oneYearAgo}'`) for most searches. This excludes ancient cruft while finding relevant docs.

### Combined Query Patterns

**Find recent docs about a topic:**
```
fullText contains 'topic' and modifiedTime > '2025-01-01' and mimeType = 'application/vnd.google-apps.document'
```

**Find all file types about a topic (excluding folders):**
```
fullText contains 'topic' and not mimeType = 'application/vnd.google-apps.folder'
```

**Find in a specific folder tree:**
```
'FOLDER_ID' in parents and fullText contains 'topic'
```

---

## Translation Cheat Sheet

When Claude (or a user) uses natural language, translate to API syntax:

| Natural Language | Drive API | Gmail API |
|-----------------|-----------|-----------|
| "Find docs about X" | `fullText contains 'X'` | `X` |
| "From John" | Not supported | `from:john` |
| "Starred items" | `starred = true` | `is:starred` |
| "Modified this month" | `modifiedTime > '2025-12-01'` | `newer_than:30d` |
| "PDFs only" | `mimeType = 'application/pdf'` | `filename:*.pdf` or `has:attachment` |
| "Shared with me" | `sharedWithMe = true` | N/A (Gmail has no equivalent) |
| "In project folder" | `'FOLDER_ID' in parents` | `label:project` (if labeled) |

---

## The Action Items Gap (followup:actionitems)

**What it does in UI:** Shows docs where you're @mentioned in unresolved comments.

**Why it fails via API:** The Drive API doesn't expose this filter. It's a UI-only feature that queries a separate internal index.

### How Action Items Actually Work

Action items = comments with `@mentions`. When you @mention someone in a Google Doc comment:
- It creates an "action item" for that person
- Shows up in their `followup:actionitems` search (UI only)
- The comment has `mentionedEmailAddresses` field when fetched via API

### Solution: Drive Activity API

With `drive.activity.readonly` scope, we can query comment events directly:

```python
from adapters.services import get_activity_service

activity = get_activity_service()

# Find comment activity with mentions
result = activity.activity().query(
    body={
        "pageSize": 50,
        "filter": "detail.action_detail_case:COMMENT",
    }
).execute()

for act in result.get("activities", []):
    for action in act.get("actions", []):
        comment = action.get("detail", {}).get("comment", {})
        if "mentionedUsers" in comment:
            # This is an action item
            target = act["targets"][0]["driveItem"]
            print(f"Action item in: {target.get('title')}")
```

**Cost:** Single API call returns all recent comment activity. Much more efficient than N+1.

### Alternative: Google Tasks API

Tasks assigned from Docs appear in Google Tasks with `showAssigned=True`:

```python
from adapters.services import get_tasks_service

tasks = get_tasks_service()
result = tasks.tasks().list(
    tasklist="@default",
    showAssigned=True,  # Include doc-assigned tasks
).execute()
```

---

## Extended Capabilities (Beyond UI)

With expanded scopes, mise-en-space can do things humans can't easily do in the UI:

### Calendar + Drive Correlation

Find docs linked to meetings:

```python
from adapters.services import get_calendar_service

calendar = get_calendar_service()
events = calendar.events().list(
    calendarId="primary",
    timeMin=week_ago,
    timeMax=now,
).execute()

for event in events.get("items", []):
    attachments = event.get("attachments", [])
    # These are Drive files linked to the meeting
    for att in attachments:
        print(f"Meeting '{event['summary']}' has doc: {att['title']}")
```

### Drive Labels API

Query files by organizational labels:

```python
from adapters.services import get_labels_service

labels = get_labels_service()
result = labels.labels().list(view="LABEL_VIEW_FULL").execute()

# Available labels: CCID, Contracts, Information Classification Policy, etc.
```

### Activity Timeline

See who did what on any file:

```python
result = activity.activity().query(
    body={
        "itemName": "items/FILE_ID",  # Specific file
        "pageSize": 50,
    }
).execute()

# Returns: edits, views, shares, comments, moves, etc.
```

---

## Implementation Notes

1. **Drive search has no snippets** — `fullText` finds files but doesn't tell you *where* the match is. Consider fetching/extracting to show context.

2. **Gmail's `resultSizeEstimate` lies** — Returns 201 for large result sets regardless of actual count. Use for "has results" check, not precise counts.

3. **Drive's `from:/to:` gap is real** — There's no API way to find "files John shared with me" or "files I shared with Jane." The UI can do this, but it's not exposed.

4. **Relative dates: Gmail yes, Drive no** — Gmail accepts `newer_than:7d`, Drive needs `modifiedTime > '2025-01-24'`. Calculate the date when building queries.

5. **viewedByMeTime works** — If you want "recently viewed" docs, `viewedByMeTime > '2025-01-01'` is a valid filter.

---

## Sources

Official documentation:
- [Drive API Search Files](https://developers.google.com/drive/api/guides/search-files)
- [Drive API Query Terms Reference](https://developers.google.com/drive/api/guides/ref-search-terms)
- [Gmail API Filtering Messages](https://developers.google.com/gmail/api/guides/filtering)
- [Gmail Search Operators (support)](https://support.google.com/mail/answer/7190)

Community resources:
- [Gmail operators gist](https://gist.github.com/msikma/a5042685efe1c95dc4d36f319a527f62) — comprehensive list
- [GAT Knowledge Base](https://gatlabs.com/knowledge/tech-tips/show-all-action-items-assigned-in-google-drive/) — action items discovery
- [bugwarrior #741](https://github.com/ralphbean/bugwarrior/issues/741) — discussion of API limitations
