# The Cross-Source Exploration Loop

**Core insight: Files are artifacts. Emails are meaning.**

A document tells you *what* was decided. The email thread tells you *why*, *who pushed back*, and *what concerns remain*.

## The Loop

```
1. Search Drive for topic → find file
2. Search Gmail `filename:X` → find email that sent it
3. Read email → discover new terms, people, related files
4. Search Drive with new terms → repeat
```

This loop discovers the **meaning** (in communications) behind **artifacts** (files).

## Following email_context Hints

When search returns a Drive file, check for `email_context`:

```json
{
  "id": "1abc...",
  "name": "Strawman Framework.docx",
  "email_context": {
    "message_id": "19b2d00b5124952e",
    "from": "Anthony.Jones@thinkbox.tv",
    "subject": "Lantern - data & privacy"
  }
}
```

**Don't ignore this.** The email thread contains the discussion around the document.

```python
# Found doc with email_context? Fetch the email too
fetch("19b2d00b5124952e")
```

## The Sous-Chef Pattern

When you fetch a doc/sheet/slides, mise automatically deposits:
- `content.md` — the extracted content
- `comments.md` — open comments (if any)
- `manifest.json` — metadata including `open_comment_count`

**Always check for `comments.md`.** Open comments often contain the real discussion — disagreements, questions, suggestions that didn't make it into the final text.

```bash
# After fetching, check what's in the deposit
ls mise-fetch/doc--strawman-framework--1abc123/
# content.md  comments.md  manifest.json

# Read the comments — they might be more valuable than the doc itself
```

## When to Stop

The loop ends when:
- You've found the canonical source document
- You understand the key decision-makers and their positions
- You've traced the context back to its origin
- The user has enough to proceed

**Don't exhaust every thread.** The goal is understanding, not completeness.
