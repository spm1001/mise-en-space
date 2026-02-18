# Sheet Creation: Design Exploration

**Date:** 2026-02-16
**Status:** Shipped — single-tab (CSV upload) and multi-tab (Sheets API hybrid) creation both live. Deposit-then-publish (`source=path`) and inline `content=` both work. Per-tab CSV deposits, raw xlsx preservation, and formula pass-through landed. Integration tests pending (mise-nihiwa, mise-sokedi).

## The Core Insight

The calling Claude almost never generates tabular data from thin air. The numbers come from somewhere: a BigQuery query, a fetched spreadsheet, an email attachment, a scraped web table. The data is already on disk (or could be trivially deposited there) before the creation call happens.

This means the input to `do(operation="create", doc_type="sheet")` should be a **file path**, not inline content. The same deposit-then-reference pattern that makes fetch token-efficient applies in reverse.

## Design Principle: Deposit-Then-Publish

The pattern is universal across all `do(operation="create")` doc types:

1. Claude deposits content to `mise-do/{type}--{slug}--draft/`
2. Human can inspect, request edits, or edit directly
3. Claude calls `do(operation="create", source="mise-do/...")` to publish
4. Tool reads the deposit, creates the Google Workspace document, enriches the manifest with the result

This creates a **checkpoint the human can interact with**. The file on disk is a draft. The `do()` call is a publish. Draft → review → publish is better than compose-and-publish-in-one-shot.

### Single Workspace Directory

`mise/` is the workspace — bidirectional. Fetch deposits there, do reads from there. The direction is implied by the operation, not the folder structure.

This avoids a copy step when the flow is fetch → transform → create: the caller writes transformed data back to `mise/` and points `do(source=...)` at it.

**Replaces:** the earlier `mise-fetch/` + `mise-do/` split. A single folder is simpler and matches the kitchen metaphor — mise-en-place is one station, not two.

### Token Economics

Writing to file then referencing by path is **2 context burns instead of 3**:

1. Reasoning that produces the content (unavoidable)
2. Write tool call — content flows to disk (unavoidable)
3. ~~Inline in `do()` tool call~~ → replaced by ~15-token file path

The saving scales with data size. A 500-row CSV as inline content is ~5,000 tokens. As a path, it's 15.

### The Edit Advantage

Surgical edits are cheap (~20 tokens for an Edit tool call). The deposit-first pattern enables:

```
Write → human reads → "change the header" → Edit → human approves → do(source=path)
```

Every step is cheap. Full document generation happens once.

## Three Candidate Paths

Discovered via `about.get(fields='importFormats')` — Drive natively imports `text/csv` → Google Sheet, same pattern as `text/markdown` → Google Doc (which we already use for doc creation).

### Path A: Drive CSV Upload (simplest)

Upload CSV to Drive with target mimeType `application/vnd.google-apps.spreadsheet`. Drive converts natively. **Identical pattern to existing doc creation.**

```python
media = MediaIoBaseUpload(
    io.BytesIO(csv_content.encode("utf-8")),
    mimetype="text/csv",
    resumable=True,
)
file_metadata = {"name": title, "mimeType": GOOGLE_SHEET_MIME}
service.files().create(body=file_metadata, media_body=media, ...)
```

- **Formatting:** None specified — Google auto-detects types (numbers, dates, currency patterns)
- **API calls:** 1
- **Dependencies:** None (reuses existing Drive adapter)
- **Multi-tab:** Not directly — CSV is single-sheet. Would need multiple uploads + merge, or switch to XLSX/Sheets API for multi-tab.
- **Formulas:** Handled by Google's auto-detection (cells starting with `=` treated as formulas)

### Path B: Sheets API `spreadsheets.create` (full control)

Single POST creates a fully formatted, multi-tab spreadsheet with values, formatting, frozen rows, column widths, number formats — everything in one call.

- **Formatting:** Full explicit control via `userEnteredFormat` on each cell
- **API calls:** 1 (+ optional 1 for `autoResizeDimensions` — can't be done in create)
- **Dependencies:** Sheets API scope (already have it for reading)
- **Multi-tab:** Native — `sheets[]` array
- **Formulas:** Via `formulaValue` in `ExtendedValue`

The JSON is verbose (~15 lines per formatted cell) but the *caller* never sees it — our assembly layer builds the resource dict from parsed CSV + type info.

Key capabilities confirmed via discovery doc:
- `sheets[].data[].rowData[].values[].userEnteredFormat.numberFormat` (currency, percent, date)
- `sheets[].properties.gridProperties.frozenRowCount`
- `sheets[].data[].columnMetadata[].pixelSize` (column widths)
- `sheets[].bandedRanges[]` (zebra stripes)
- `properties.locale` (set to `en_GB` for GBP default)
- `properties.defaultFormat` (spreadsheet-wide default cell format)

Only gap: `autoResizeDimensions` requires a separate `batchUpdate` call.

### Path C: openpyxl + Upload+Convert (XLSX intermediary)

Build XLSX locally with openpyxl, upload to Drive with `convert=True`.

- **Formatting:** Whatever survives XLSX → Sheet conversion (bold, widths, number formats all transfer well)
- **API calls:** 1 upload
- **Dependencies:** openpyxl
- **Multi-tab:** Native in XLSX
- **Formulas:** Written as strings, Google evaluates on import
- **Timing:** Upload+convert dominates at 4-7s (benchmarked for DOCX/XLSX in Feb 2026)

### Comparison

| Dimension | A: Drive CSV | B: Sheets create | C: openpyxl+upload |
|-----------|-------------|-------------------|-------------------|
| API calls | 1 | 1-2 | 1 |
| Formatting control | None (auto-detect) | Full | Good (conversion-dependent) |
| Multi-tab | No | Yes | Yes |
| New dependency | None | None | openpyxl |
| Code complexity | ~20 lines | ~100 lines | ~60 lines + dep |
| Latency | ? (benchmark) | ? (benchmark) | 4-7s (known) |

### Progressive Enhancement Strategy

These paths aren't mutually exclusive. They form a progression:

1. **Start with Path A** — Drive CSV upload. Identical to doc creation. Google handles type detection. Ship fast.
2. **Add formatting pass** — follow-up `spreadsheets.batchUpdate` for bold headers, frozen row, number formats. Still no new dependency. This is Path A + a formatting layer.
3. **Path B for multi-tab / rich formatting** — when the manifest specifies multiple sheets or explicit column types, use `spreadsheets.create` for full control.
4. **Path C as format-detection branch** — if the caller deposits an `.xlsx` directly (from xlsx skill or transformed attachment), upload+convert. openpyxl only needed if we build XLSX internally, which we might never need.

**Benchmark first** — the choice between A, A+formatting, and B depends on timing and quality data we don't have yet.

## Drive Import Formats Reference

From `about.get(fields='importFormats')` (live query, Feb 2026):

**Sources that convert to Google Sheet:**
| Source MIME | Format |
|-------------|--------|
| `text/csv` | CSV |
| `text/tab-separated-values` | TSV |
| `text/comma-separated-values` | CSV (alt) |
| `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | XLSX |
| `application/vnd.ms-excel` | XLS |
| `application/vnd.oasis.opendocument.spreadsheet` | ODS |
| `application/vnd.ms-excel.sheet.macroenabled.12` | XLSM |
| + templates for XLSX and XLSM | |

**TSV note:** `text/tab-separated-values` is also a native import. Useful when data contains commas in values — avoids quoting complexity.

## Sheet-Specific Design

### Deposit Structure

**Single tab (most common):**
```
mise-do/sheet--q4-analysis--draft/
├── content.csv
└── manifest.json
```

**Multi-tab:**
```
mise-do/sheet--full-report--draft/
├── manifest.json
├── summary.csv
├── detail_q1.csv
└── detail_q2.csv
```

### Manifest Schema

```json
{
  "title": "Q4 Analysis",
  "doc_type": "sheet",
  "folder_id": "optional-drive-folder-id",
  "sheets": [
    {
      "name": "Summary",
      "file": "summary.csv",
      "columns": ["text", "currency:GBP", "percent", "number"]
    },
    {
      "name": "Q1 Detail",
      "file": "detail_q1.csv"
    }
  ]
}
```

For single-tab, `sheets` can be omitted — the tool finds `content.csv` and uses the title as the tab name.

### Column Type Vocabulary

Small and memorable. Seven types:

| Type | Format applied | Auto-detected from |
|------|---------------|-------------------|
| `text` | (default) | — |
| `number` | `#,##0` | Numeric strings |
| `currency:CODE` | `£#,##0.00` (locale-aware) | `£`, `$`, `€` prefixes |
| `percent` | `0.00%` | `%` suffix |
| `date` | Auto-detect | ISO dates, common formats |
| `date:FORMAT` | Explicit pattern | — |
| `boolean` | Checkbox | `TRUE`/`FALSE` strings |

If `columns` is omitted, the tool infers types from data patterns.

### What the Tool Auto-Decides (caller never specifies)

- **Bold headers** — first row, always
- **Frozen header row** — always (easy to unfreeze manually)
- **Auto-width columns** — always better than defaults
- **Number detection** — if it looks like a number, store as number
- **Date detection** — if it looks like a date, store as date
- **Right-align numbers** — Sheets does this natively

### Formula Handling

Cells starting with `=` are treated as formulas. This is the universal spreadsheet convention and costs zero overhead. The caller writes `=SUM(B2:B4)` in the CSV; the tool passes it through with `USER_ENTERED` value input option (or Google auto-detects on Drive CSV import).

Cross-tab references (`='Detail'!A1`) work the same way.

### What's Out of Scope (v1)

- **Charts** — token cost of expressing chart config is enormous relative to value
- **Conditional formatting** — too expensive to express
- **Data validation / dropdowns** — maybe v2
- **Cell-level formatting** (colours, fonts, borders beyond headers) — tool is opinionated
- **Named ranges** — too structural for creation

These are better served by iterative editing (`do(operation="edit")`) or the user working in the Sheets UI.

## After Creation: The Receipt

The manifest gets enriched:

```json
{
  "title": "Q4 Analysis",
  "doc_type": "sheet",
  "status": "created",
  "file_id": "1abc...",
  "web_link": "https://docs.google.com/spreadsheets/d/1abc.../edit",
  "created_at": "2026-02-16T13:30:00Z"
}
```

The deposit folder becomes a receipt — what was published, when, where it went. Same philosophy as fetch manifests.

## Broader Pattern: All Creation Through Deposits

This isn't sheets-only. The deposit-then-publish pattern applies to all doc types:

| Doc type | Deposit format | Notes |
|----------|---------------|-------|
| `doc` | `content.md` | Markdown → Google Doc (already works via Drive import) |
| `sheet` | `content.csv` or multi-CSV + manifest | CSV → Google Sheet |
| `slides` | Content spec (future) | Structured → Google Slides |

The `source` parameter on `do(operation="create")` points at the deposit folder. The manifest's `doc_type` routes to the right builder. Inline `content` becomes a convenience shortcut for trivial cases, not the guided path.

## Benchmark Results (Feb 2026)

Benchmarked on Kube (Raspberry Pi, Kubernetes). All paths hit the same network, so relative timing is fair. Script: `scripts/sheet_creation_benchmark.py`.

### Type Detection Accuracy (36 diverse cell types)

| Path | Accuracy | Score |
|------|----------|-------|
| **A: Drive CSV upload** | **30/32 (94%)** | Winner |
| **A+: Drive CSV + format** | **30/32 (94%)** | Same detection + bold/freeze |
| **B: Sheets API create** | **19/32 (59%)** | Our parser loses type info |
| **C: openpyxl + upload** | **10/32 (31%)** | Everything stored as strings |

### What Google's CSV Import Gets Right (Path A)

| Type | Result | Notes |
|------|--------|-------|
| Plain text, unicode, emoji | ✓ string | All preserved correctly |
| Integers, decimals, negatives | ✓ number | Including comma-formatted (`1,245,000`) |
| **£ currency** | ✓ number/currency | Recognised and formatted |
| **€ currency** | ✓ number/currency | Recognised and formatted |
| **Percentages** | ✓ number/percent | `3.63%` → stored as `0.0363` with percent format |
| **ISO dates** | ✓ date | `2026-01-15` recognised |
| **UK dates** | ✓ date | `15/01/2026` recognised |
| **Date with time** | ✓ date | `2026-01-15 14:30:00` recognised |
| **Short dates** | ✓ date | `15-Jan-2026` recognised |
| Booleans | ✓ boolean | TRUE/FALSE detected |
| Formulas | ✓ formula | =SUM, =AVERAGE, =IF all evaluated |
| Empty cells | ✓ empty | Correct |

### What Google Gets Wrong

| Type | Result | Why |
|------|--------|-----|
| **$ currency** | ✗ stored as string | `en_GB` locale doesn't auto-detect USD. £ and € work. |
| **US dates** | ✗ stored as string | `01/15/2026` ambiguous in `en_GB` — month 15 doesn't exist, so it fails rather than guessing. Correct behaviour. |

### Ambiguous Cases (informational — no "right" answer)

| Type | Path A result | Path C (openpyxl) result | Notes |
|------|--------------|--------------------------|-------|
| Phone `07700900123` | number `7700900123` | string `07700900123` | A strips leading zero — bad for phones |
| Leading zeros `007` | number `7` | string `007` | A strips — bad for IDs |
| ID `00012345` | number `12345` | string `00012345` | A strips — bad for IDs |
| Year `2026` | number `2026` | string `2026` | Could go either way |

**Key insight:** Leading zeros are the one case where CSV-as-strings (Path C) wins. The manifest `columns` array can handle this: `"text"` forces string interpretation. But for the 80% case, Google's auto-detection is dramatically better.

### Why Path B Scored Poorly

Our `make_cell_value` parser in Path B strips currency symbols and percent signs to find the underlying number. This means `£12,450.00` becomes `numberValue: 12450.0` — correct number, but Google doesn't know it's currency. We'd need to explicitly set `numberFormat: {type: "CURRENCY", pattern: "£#,##0.00"}` on each cell. That's exactly the verbosity problem we wanted to avoid.

Path A avoids this entirely — Google's import engine does the type detection AND applies the right format in one step.

### Timing (500 rows × 10 cols, mixed types including £ currency and percentages)

| Path | Total | Breakdown |
|------|-------|-----------|
| **A: Drive CSV** | **4.34s** | Single API call |
| **A+: Drive CSV + format** | **6.01s** | Upload 4.61s + format pass 1.40s |
| **B: Sheets API create** | **4.30s** | Create 2.70s + auto-resize 1.60s |
| **C: openpyxl + upload** | **5.07s** | Build 0.04s + upload 5.03s |

Path A and B are tied on speed (~4.3s). The formatting pass (A+) adds ~1.5s for bold headers, frozen row, and auto-resize — worth it for professional output.

### At Scale (5000 rows × 15 cols, 720 KB CSV)

| Path | Total | Breakdown |
|------|-------|-----------|
| **A: Drive CSV** | **6.02s** | Single API call — scales linearly |
| **A+: Drive CSV + format** | **17.08s** | Upload 6.35s + format **8.30s** (auto-resize expensive at scale) |
| **B: Sheets API create** | **23.75s** | Create 16.71s + resize 7.04s (JSON payload enormous) |

The formatting pass costs 8.3s at 5000 rows — auto-resize measures text widths across 75,000 cells. Bold headers and frozen rows are nice-to-haves the user can do in one click.

Spot check on Path A's type detection at 5000 rows:
- `TXN-000001` → string (dash prevents number detection)
- `2026-11-04` → date
- Quantities → number
- `£1,123.82` → currency
- Customer names with commas, accents → string

Flawless.

### Tick Prefix for Forcing Text

Drive CSV import respects the `'` (tick) prefix — same convention as the Sheets UI. Tested Feb 2026:

| CSV Value | Stored As | Display |
|-----------|-----------|---------|
| `007` | number `7` | 7 |
| `'007` | string `007` | 007 |
| `07700900123` | number `7700900123` | 7700900123 |
| `'07700900123` | string `07700900123` | 07700900123 |

The tick doesn't display in the Sheet — Google treats it as a type hint and hides it. If the manifest marks a column as `text`, the tool can prepend `'` to numeric-looking values before upload.

### Recommendation

**Path A (Drive CSV upload) is the only path needed for v1.**

- 94% type detection accuracy with zero custom parsing
- Same code pattern as existing doc creation (`text/markdown` → Doc)
- 6s for 5000 rows — scales linearly
- ~10 lines of new code beyond existing `_create_doc`
- No new dependencies
- No formatting pass needed — bold headers / frozen row are one-click in the UI

**Path B (Sheets API create) is a future option for multi-tab.** CSV upload is single-sheet. When multi-tab is needed, Path B creates all tabs in one call. But that's a v2 concern.

**Path C (openpyxl) is not needed as an internal assembly layer.** Only useful as a passthrough for `.xlsx` deposits (already handled by `upload_and_convert()`).

## Open Questions

1. **Path A multi-tab workaround** — Could we upload a CSV per tab and add sheets programmatically? Or is Path B the clean answer for multi-tab? Worth testing: create via Path A, then use `spreadsheets.batchUpdate` with `addSheet` + `values.batchUpdate` for additional tabs.

2. **$ currency in en_GB** — Users working with USD data won't get currency formatting via Path A. Options: (a) detect $ in CSV and apply format via batchUpdate, (b) accept the limitation — user can format manually, (c) manifest `columns: ["currency:USD"]` triggers explicit formatting.

3. **Manifest schema sharing** — fetch manifests describe what was retrieved; do manifests describe what to build (then what was built). Same fields? Same schema with different `status` values? Or independent?

4. **`mise-do/` lifecycle** — when do draft folders get cleaned up? After successful creation? After N days? Never (they're receipts)?

5. **Does this change the existing `do(operation="create", doc_type="doc")` contract?** Currently it takes inline `content`. Adding `source` as an alternative (and preferred) path is backwards-compatible. The inline path stays for trivial cases but the skill/docs guide callers toward deposit-first.

## Sheets API Reference

### Two Write Surfaces

**`spreadsheets.values.batchUpdate`** — values only, compact:
```python
body = {
    "valueInputOption": "USER_ENTERED",
    "data": [{"range": "Sheet1!A1:C3", "values": [[1, 2, 3], [4, 5, 6]]}]
}
```

**`spreadsheets.batchUpdate`** — everything (formatting, structure, charts, and values via `UpdateCellsRequest`):
- 40+ request types
- Atomic — all-or-nothing
- One API call = one quota hit regardless of sub-request count

### Key Quotas
- 300 write requests per minute per project
- 100 requests per 100 seconds per user
- Recommended max 2MB payload

### `spreadsheets.create` — Full Resource Creation
Creates a fully populated, formatted, multi-tab spreadsheet in one POST. Accepts the full `Spreadsheet` resource. Everything except `autoResizeDimensions` can be set in the create call. Confirmed via discovery doc (Feb 2026).
