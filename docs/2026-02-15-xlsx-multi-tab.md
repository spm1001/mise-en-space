# Field Report: XLSX multi-tab fetch returns only first tab

**Date:** 2026-02-15
**Artifact:** OHID Survey - Raw Data.xlsx (`1ZCmdo3OE46hROf2Mr8_irqWeaIjbK_Md`)
**Context:** User fetched a 2-tab xlsx via mise. Only tab 1 appeared in the deposit.

## What happened

1. `fetch("1ZCmdo3OE46hROf2Mr8_irqWeaIjbK_Md")` — xlsx with 2 tabs (raw data + a second sheet)
2. Deposit: `mise/xlsx--ohid-survey-raw-data-xlsx--1ZCmdo3OE46h/content.csv`
3. content.csv contains only tab 1's data. No sheet headers, no second tab.

## Root cause

The xlsx path (`adapters/office.py` → `adapters/conversion.py`) uploads to Drive as a Google Sheet, then exports as `text/csv`. **Drive's CSV export only returns the first sheet.** This is a known Google API limitation — the export endpoint has no multi-sheet CSV mode.

## Second bug: native Sheets path

User then converted the xlsx to a native Google Sheet and fetched that. Both tabs appeared, but concatenated into a single `content.csv` with `=== Sheet: Name ===` headers interleaved. These headers are not valid CSV — any tool that reads the file will choke on them.

The Sheets adapter (`fetch_spreadsheet`) correctly gets all tabs via `batchGet()`, and the extractor (`extract_sheets_content`) formats them with sheet headers. The format is designed for LLM consumption (Claude can parse the headers) but breaks CSV tooling.

## Fix direction

After xlsx → Google Sheet conversion, use `fetch_spreadsheet()` + `extract_sheets_content()` instead of Drive CSV export. This gets all tabs. Then decide multi-tab deposit format:
- Separate `.csv` per sheet (clean for tools, more files)
- Single file with clear delimiters (convenient for Claude, breaks CSV parsers)

Tracked as mise-lofeho under mise-wocidi.
