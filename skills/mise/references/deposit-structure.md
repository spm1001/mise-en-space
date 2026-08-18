# Deposit Structure

Content fetched by mise lands in `.mise/` (dot-named — hidden from casual browsing) in your working directory (when `base_path` is passed correctly).

## Folder Naming

`{type}--{title-slug}--{id-prefix}/`

| Type | Example |
|------|---------|
| doc | `doc--meeting-notes--abc123def/` |
| sheet | `sheet--budget-2026--xyz789abc/` |
| slides | `slides--ami-deck-2026--1OepZjuwi/` |
| gmail | `gmail--re-project-update--thread456/` |
| pdf, xlsx, docx | `pdf--quarterly-report--abc123/` |

## Standard Deposit

```
.mise/{type}--{title}--{id}/
├── manifest.json           # Metadata: type, title, id, fetched_at, warnings
├── content.md              # Extracted text/markdown
└── comments.md             # Open comments (if any exist)
```

## manifest.json

Self-describing metadata. Key fields:

| Field | Purpose |
|-------|---------|
| `type` | doc, sheet, slides, gmail, pdf, etc. |
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

## PDFs: Exhibit Crops and Anchors (the two-repo contract)

A census of real corporate PDFs (636 probes, 70 documents, 2026-08-17) measured ~3% of values as **vision-only** — printed inside embedded chart images (Excel charts pasted as pictures) that NO text extractor can reach. PDF deposits therefore carry the qualifying embedded graphics as crop files, each announced by a grep-able anchor in `content.md` at the page where the graphic sits:

```
pdf--strategy-update--abc123/
├── content.md              # pdftotext -layout text, with exhibit anchors
├── crop_p008_i012.png      # embedded graphic from page 8 (original resolution)
├── page_01.png …           # full-page thumbnails (when thumbnails=True)
└── manifest.json           # includes crops [{file, pages, width, height}], crop_count
```

**The anchor line** (one per crop per page, at the end of that page's text):

```
<!-- exhibit: crop_p008_i012.png | page 8 | 751x452px | embedded graphic — its values are NOT in this text; view the crop image -->
```

**The two-stage retrieval this enables:** stage 1, grep/read the text as usual; on hitting an anchor whose graphic might hold the answer, stage 2, view the named crop file with a vision-capable read. The anchor prefix is stable — `grep 'exhibit:' content.md` lists every graphic with its page and file in one hit each.

**Contract guarantees, and their honest limits:**

- Anchors carry **only deterministic fields** (file, page, pixel dimensions). Semantic fields — what the chart shows, its entities and metrics — would require understanding the image, which the extractor cannot do without fabricating; enrich them consumer-side from the crops if you need an index.
- Crops cover **embedded raster objects** passing a corpus-calibrated filter (min dimension 240px, on ≤3 pages, covering <80% of the page). Vector-drawn charts, full-page background photos and repeated furniture (logos, watermark badges) are excluded — full-page values remain reachable via the page thumbnails.
- When page markers are absent from an extraction (markitdown/Drive fallback paths), anchors group in a disclosed block at the end of `content.md` instead of at their pages, and a warning cue says so.
- No count cap: every qualifying graphic ships (census: median ~4 per document, p90 ~44 on photo-heavy decks).

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

This creates a separate deposit: `.mise/xlsx--budget--thread_id/`

## Large Deposits

For big email threads (32k+ tokens) or long docs, preview before full Read:

```bash
# First 50 lines
head -50 .mise/gmail--re-lantern--abc123/content.md

# Grep for topic
grep -A5 "controllership" .mise/gmail--*/content.md

# Count messages in thread
grep -c "^## Message" .mise/gmail--*/content.md
```
