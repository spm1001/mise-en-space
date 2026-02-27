# Deposit Structure

Content fetched by mise lands in `mise/` in your working directory (when `base_path` is passed correctly).

## Folder Naming

`{type}--{title-slug}--{id-prefix}/`

| Type | Example |
|------|---------|
| doc | `doc--meeting-notes--abc123def/` |
| sheet | `sheet--budget-2026--xyz789abc/` |
| slides | `slides--ami-deck-2026--1OepZjuwi/` |
| gmail | `gmail--re-project-update--thread456/` |
| web | `web--python-one-shot-tools--3364f7aa/` |
| pdf, xlsx, docx | `pdf--quarterly-report--abc123/` |

## Standard Deposit

```
mise/{type}--{title}--{id}/
├── manifest.json           # Metadata: type, title, id, fetched_at, warnings
├── content.md              # Extracted text/markdown
└── comments.md             # Open comments (if any exist)
```

## manifest.json

Self-describing metadata. Key fields:

| Field | Purpose |
|-------|---------|
| `type` | doc, sheet, slides, gmail, web, pdf, etc. |
| `title` | Original document title |
| `id` | Full source ID |
| `fetched_at` | ISO timestamp |
| `open_comment_count` | Unresolved comments (0 = no comments.md) |
| `warnings` | Extraction issues (empty sheets, truncation, etc.) |

## Sheets / XLSX: Per-Tab CSVs

```
sheet--budget-2026--xyz789abc/
├── content.csv             # Combined: all tabs (=== Sheet: Name === headers)
├── content_revenue.csv     # Per-tab: one file per tab (multi-tab only)
├── content_costs.csv
├── comments.md             # Open comments (if any)
└── manifest.json           # includes tabs [{name, filename}], formula_count
```

Multi-tab spreadsheets deposit one CSV per tab alongside the combined `content.csv`. Single-tab sheets just get `content.csv`.

XLSX deposits also include the original `.xlsx` file (original filename preserved) for roundtrip workflows (edit and re-upload without conversion loss). The `formula_count` cue tells you when the raw file matters.

## Slides: Thumbnails

```
slides--ami-deck--1Oep/
├── content.md
├── comments.md
├── slide_01.png            # 1-indexed, zero-padded
├── slide_02.png
└── manifest.json           # includes slide_count, has_thumbnails
```

Only slides needing visual context get thumbnails (charts, complex layouts, images). Text-only slides are skipped.

## Gmail: Attachments

```
gmail--re-project-update--abc123/
├── content.md              # Thread conversation text
├── quarterly-report.pdf    # Original attachment binary
├── quarterly-report.pdf.md # Extracted text from PDF
├── chart.png               # Image attachment (as-is)
└── manifest.json           # includes attachments list
```

**In content.md**, extracted attachments appear as pointers:
```
**Extracted attachments:**
- quarterly-report.pdf → `quarterly-report.pdf.md`
- chart.png (deposited as file)
```

**What's eagerly extracted:** PDFs, images
**What's skipped:** Office files (DOCX/XLSX/PPTX) — 5-10s each

Extract skipped attachments on demand:
```python
fetch("thread_id", attachment="budget.xlsx", base_path="...")
```

This creates a separate deposit: `mise/xlsx--budget--thread_id/`

## Large Deposits

For big email threads (32k+ tokens) or long docs, preview before full Read:

```bash
# First 50 lines
head -50 mise/gmail--re-lantern--abc123/content.md

# Grep for topic
grep -A5 "controllership" mise/gmail--*/content.md

# Count messages in thread
grep -c "^## Message" mise/gmail--*/content.md
```
