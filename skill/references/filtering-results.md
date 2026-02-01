# Filtering Large Search Results

When search returns many results, you don't need to Read the entire JSON file. Filter first, read selectively.

## The Problem

```python
# Search returns: {"path": "mise-fetch/search--lantern--2026-01-31.json", "drive_count": 20, "gmail_count": 15}
# That's 35 results across ~24KB of JSON

# BAD: Read everything, consume 6K+ tokens
Read("mise-fetch/search--lantern--2026-01-31.json")
```

## Filter Patterns

### Preview first few results
```bash
# See first 5 Drive results (names and IDs only)
jq '.drive_results[:5] | .[] | {name, id}' mise-fetch/search--lantern--*.json
```

### Filter by filename pattern
```bash
# Find files with "strawman" in the name
jq '.drive_results[] | select(.name | test("strawman"; "i")) | {name, id, url}' mise-fetch/search--*.json
```

### Filter by MIME type
```bash
# Find only Google Docs (not sheets, slides, PDFs)
jq '.drive_results[] | select(.mimeType | contains("document")) | {name, id}' mise-fetch/search--*.json
```

### Filter by recency
```bash
# Find files modified in last 30 days
jq '.drive_results[] | select(.modified > "2026-01-01") | {name, id, modified}' mise-fetch/search--*.json
```

### Get just the IDs for fetching
```bash
# Extract IDs to fetch
jq -r '.drive_results[:5] | .[].id' mise-fetch/search--*.json
```

### Gmail: filter by sender
```bash
# Find threads from a specific person
jq '.gmail_results[] | select(.from | contains("anthony")) | {subject, thread_id, date}' mise-fetch/search--*.json
```

## When to Filter vs Read All

| Scenario | Action |
|----------|--------|
| `drive_count` + `gmail_count` < 10 | Just Read the file |
| `drive_count` > 15 | Filter by name/type first |
| Looking for specific person | Filter by `from`/`owners` |
| Looking for specific doc type | Filter by `mimeType` |
| Research task (need everything) | Read in chunks, triage with user |

## Integration with Fetch

After filtering, fetch the files you actually need:

```bash
# Get IDs of promising files
jq -r '.drive_results[] | select(.name | test("framework"; "i")) | .id' mise-fetch/search--*.json
# Output: 1abc123def

# Fetch those specific files
fetch("1abc123def")
```

## Remember

The search deposit pattern gives you the opportunity to be selective. Use it.
