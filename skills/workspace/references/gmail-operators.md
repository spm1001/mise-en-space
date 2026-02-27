# Gmail Search Operators

Use these instead of keyword soup. Targeted searches find relevant threads faster.

## Most Useful Operators

| Operator | Example | Finds |
|----------|---------|-------|
| `from:` | `from:alice@example.com` | Emails from Alice |
| `to:` | `to:team@company.com` | Emails to the team |
| `filename:` | `filename:report.pdf` | Emails with attachment named report.pdf |
| `has:attachment` | `has:attachment budget` | Emails about budget with any attachment |
| `after:` | `after:2026/01/01` | Emails after Jan 1, 2026 |
| `before:` | `before:2026/02/01` | Emails before Feb 1, 2026 |
| `subject:` | `subject:Q4 planning` | Emails with Q4 planning in subject |
| `in:` | `in:sent` | Emails you sent |
| `is:` | `is:starred` | Starred emails |

## Combining Operators

```
from:alice@example.com after:2026/01/01 subject:budget
filename:strawman from:legal@company.com
has:attachment to:team@company.com after:2025/12/01
```

## Common Patterns

### Find the email that sent a document
```
filename:strawman-framework.docx
```

### Find recent emails from a person about a topic
```
from:anthony.jones@thinkbox.tv after:2026/01/01 lantern
```

### Find emails with attachments about a project
```
has:attachment subject:lantern after:2025/12/01
```

### Find your sent emails about a topic
```
in:sent subject:data governance after:2026/01/01
```

## What NOT to Do

**Don't:** Just throw keywords at search
```
# BAD: keyword soup
search("Elizabeth Kiernan Lantern data privacy contracts agreements")
```

**Do:** Use operators for precision
```
# GOOD: targeted search
search("from:elizabeth@privacylawunlocked.com after:2025/12/01", sources=["gmail"])
```
