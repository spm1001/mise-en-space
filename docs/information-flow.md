# mise-en-space Information Flow

## Timing Reference (Measured Jan 2026)

| Content Type | Fetch | Extract | **Total** | Notes |
|--------------|-------|---------|-----------|-------|
| **Gmail thread (text only)** | 200-250ms | <1ms | **~250ms** | Fastest path |
| **Google Doc** | 1,700ms | <1ms | **~1.7s** | API latency |
| **Google Sheet** | 920ms | <1ms | **~1s** | 2 API calls (meta + values) |
| **Google Slides (no thumbnails)** | 2,650ms | <1ms | **~2.7s** | API latency |
| **Google Slides (with thumbnails)** | 5,000ms | <1ms | **~5s** | Sequential thumbnail fetches |
| **PDF (markitdown works)** | 100-400ms | 70-850ms | **~0.5-1s** | Fast path |
| **PDF (Drive fallback)** | 100-400ms | 5-15s | **~5-15s** | Complex/scanned PDFs |
| **Office file (PPTX/DOCX/XLSX)** | 200-500ms | 5-10s | **~5-10s** | Drive conversion required |
| **Images** | 70-170ms each | ~0 | **~100ms each** | No extraction needed |

### Key Observations

1. **Gmail text is fastest** (~250ms) — good baseline for comparison
2. **Google native formats are 1-5s** — dominated by API latency, not our code
3. **Office files are the slow path** — Drive conversion unavoidable
4. **PDFs usually fast** — markitdown handles most; Drive fallback for complex ones

### Design Decision: Office Files in Email Attachments

**Problem:** Office files (PPTX/DOCX/XLSX) take 5-10s to extract via Drive conversion.

**Decision:** Don't extract Office attachments by default. Instead:
- List them in manifest with metadata
- Note "Office file — fetch separately if needed"
- Caller can explicitly request: `fetch(attachment_id)`

This keeps thread fetch fast (~1s for text + PDFs + images) while allowing caller to opt-in to slow Office extraction.

**TODO (FastMCP v2):** When async task dispatch is available, return thread immediately and stream attachment extractions as they complete.

---

## Search Flow

**Design change:** Deposit to file instead of inline JSON. More token-efficient when caller fires multiple searches.

```
┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│   CALLING CLAUDE    │    │        MISE         │    │    FILE DEPOSIT     │
├─────────────────────┤    ├─────────────────────┤    ├─────────────────────┤
│                     │    │                     │    │                     │
│ search("Project X") │───▶│ tools/search.py     │    │                     │
│                     │    │   do_search()       │    │                     │
│                     │    │     │               │    │                     │
│                     │    │     ├─▶ Drive API   │    │                     │
│                     │    │     │   (fullText)  │    │                     │
│                     │    │     │   +snippet    │    │                     │
│                     │    │     │               │    │                     │
│                     │    │     └─▶ Gmail API   │    │                     │
│                     │    │         (threads)   │    │                     │
│                     │    │         +attachments│    │                     │
│                     │    │                     │    │                     │
│                     │    │   write_results() ──────▶│ mise/          │
│                     │    │                     │    │ search--project-x/   │
│                     │    │                     │    │   └── results.json   │
│                     │    │                     │    │                     │
│ ◀─────────────────────── │ Returns:            │    │                     │
│ {                   │    │ {path, result_count}│    │                     │
│   path: "mise-      │    │                     │    │                     │
│     fetch/search-.. │    │                     │    │                     │
│   drive_count: 15,  │    │                     │    │                     │
│   gmail_count: 8    │    │                     │    │                     │
│ }                   │    │                     │    │                     │
│                     │    │                     │    │                     │
│ Grep/Read as needed │────────────────────────────────▶│ (selective read)   │
│                     │    │                     │    │                     │
└─────────────────────┘    └─────────────────────┘    └─────────────────────┘
```

---

## Fetch Flow — Google Docs/Sheets/Slides

```
┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│   CALLING CLAUDE    │    │        MISE         │    │    FILE DEPOSIT     │
├─────────────────────┤    ├─────────────────────┤    ├─────────────────────┤
│                     │    │                     │    │                     │
│ fetch("doc123")     │───▶│ tools/fetch.py      │    │                     │
│                     │    │   do_fetch()        │    │                     │
│                     │    │     │               │    │                     │
│                     │    │     ▼               │    │                     │
│                     │    │   detect_id_type()  │    │                     │
│                     │    │     → "drive"       │    │                     │
│                     │    │     │               │    │                     │
│                     │    │     ▼               │    │                     │
│                     │    │   get_metadata()    │    │                     │
│                     │    │     → mimeType      │    │                     │
│                     │    │     │               │    │                     │
│                     │    │     ▼ (route by type)    │                     │
│                     │    │   fetch_doc()       │    │                     │
│                     │    │   fetch_sheet()     │    │                     │
│                     │    │   fetch_slides()    │    │                     │
│                     │    │     │               │    │                     │
│                     │    │     ├─▶ Native API  │    │                     │
│                     │    │     │   (1-5s)      │    │                     │
│                     │    │     │               │    │                     │
│                     │    │     ▼               │    │                     │
│                     │    │   extract_*()       │    │                     │
│                     │    │     (<1ms)          │    │                     │
│                     │    │     │               │    │                     │
│                     │    │     ▼               │    │mise/          │
│                     │    │   write_content() ──────▶│ doc--title--abc123/ │
│                     │    │   write_manifest()──────▶│   ├── content.md    │
│                     │    │                     │    │   └── manifest.json │
│                     │    │                     │    │                     │
│ ◀─────────────────────── │ Returns: {path, ...}│    │                     │
│                     │    │                     │    │                     │
│ Read(content_file)  │────────────────────────────────▶│ (file read)        │
│                     │    │                     │    │                     │
└─────────────────────┘    └─────────────────────┘    └─────────────────────┘

Timing: ~1-5s depending on content type and thumbnails
```

---

## Fetch Flow — Gmail Thread (Before: THE GAP - Now Fixed)

```
The previous gap: Attachments were listed but not fetched.
Claude saw "📎 report.pdf" but couldn't read the PDF content.

This is now fixed with eager attachment extraction:
- PDFs: downloaded + extracted via markitdown/Drive
- Images: deposited alongside content.md
- Office files: skipped (too slow), listed in manifest for explicit fetch
- Trivial attachments: filtered out completely (calendar invites, vcards, small images)
```

---

## Fetch Flow — Gmail Thread (Now: With Attachments)

```
┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│   CALLING CLAUDE    │    │        MISE         │    │    FILE DEPOSIT     │
├─────────────────────┤    ├─────────────────────┤    ├─────────────────────┤
│                     │    │                     │    │                     │
│ fetch("thread789")  │───▶│ fetch_gmail()       │    │                     │
│                     │    │   │                 │    │                     │
│                     │    │   ├─▶ Gmail API     │    │                     │
│                     │    │   │   (thread)      │    │                     │
│                     │    │   │                 │    │                     │
│                     │    │   ▼                 │    │                     │
│                     │    │ For each attachment:│    │                     │
│                     │    │   │                 │    │                     │
│                     │    │   ├─ Is Office file?│    │                     │
│                     │    │   │   (PPTX/DOCX/   │    │                     │
│                     │    │   │    XLSX)        │    │                     │
│                     │    │   │                 │    │                     │
│                     │    │   ├─ Yes ───────────┼────│─▶ SKIP (note in     │
│                     │    │   │                 │    │   manifest only)    │
│                     │    │   │                 │    │                     │
│                     │    │   └─ No (PDF/img) ──┼────│─▶ Check pre-exfil   │
│                     │    │                     │    │   folder in Drive   │
│                     │    │                     │    │     │               │
│                     │    │                     │    │     ├─ Found? ──▶   │
│                     │    │                     │    │     │   fetch Drive │
│                     │    │                     │    │     │               │
│                     │    │                     │    │     └─ Not found ─▶ │
│                     │    │                     │    │         download    │
│                     │    │                     │    │         from Gmail  │
│                     │    │   ▼                 │    │                     │
│                     │    │ Combine:            │    │mise/          │
│                     │    │ - thread markdown   │    │ gmail--subject--789/│
│                     │    │ - PDF/image content │    │   ├── content.md    │
│                     │    │                     │────▶│   │   (thread +    │
│                     │    │                     │    │   │    extracted    │
│                     │    │                     │    │   │    attachments) │
│                     │    │                     │    │   └── manifest.json │
│                     │    │                     │    │       (lists Office │
│                     │    │                     │    │        files to     │
│                     │    │                     │    │        fetch later) │
│                     │    │                     │    │                     │
│ ◀─────────────────────── │ Returns: {path,     │    │                     │
│                     │    │  skipped_office:[...│    │                     │
│                     │    │ ]}                  │    │                     │
│                     │    │                     │    │                     │
│ Read(content_file)  │────────────────────────────────▶│                     │
│                     │    │                     │    │                     │
│ content.md has:     │    │                     │    │                     │
│ - messages          │    │                     │    │                     │
│ - PDF content ✓     │    │                     │    │                     │
│ - image refs ✓      │    │                     │    │                     │
│                     │    │                     │    │                     │
│ If need Office:     │    │                     │    │                     │
│ fetch("att_id")     │───▶│ (separate call,     │    │                     │
│                     │    │  5-10s extraction)  │    │                     │
│                     │    │                     │    │ ✅ FAST + COMPLETE  │
└─────────────────────┘    └─────────────────────┘    └─────────────────────┘

Timing: ~1-2s typical (text + PDFs + images)
        +5-10s per Office file if explicitly requested
```

---

## Summary

| Flow | Status | Timing | Notes |
|------|--------|--------|-------|
| Search → Drive | ✅ Fixed | ~500ms | Now includes `contentSnippet`, deposits to file |
| Search → Gmail | ✅ Fixed | ~500ms | Now includes `attachment_names`, deposits to file |
| Fetch → Doc | ✅ Works | ~1.7s | Content extracted to markdown |
| Fetch → Sheet | ✅ Works | ~1s | Content extracted to CSV |
| Fetch → Slides | ✅ Works | ~2.7-5s | Content + selective thumbnails |
| Fetch → PDF | ✅ Works | ~0.5-1s | Hybrid markitdown/Drive extraction |
| Fetch → Office | ✅ Works | ~5-10s | Via Drive conversion (slow) |
| Fetch → Video | ✅ Works | ~1s | AI summary if chrome-debug available |
| Fetch → Gmail | ✅ Works | ~250ms | Text + eager attachment extraction |
| Fetch → Gmail + Attachments | ✅ Works | ~1-2s | PDFs/images extracted, Office files skipped |

---

## Implementation Checklist

### Search Changes
- [ ] Deposit results to `mise/search--{query}/results.json`
- [ ] Return path + counts, not full JSON
- [ ] Include `contentSnippet` in Drive results ✅ (done)
- [ ] Include `attachment_names` in Gmail results ✅ (done)

### Gmail Attachment Fetch
- [x] Download attachments from Gmail API
- [x] Check "Email Attachments" Drive folder for pre-exfiltrated copies
- [x] Extract PDFs (reuse existing extractor)
- [x] Extract images (minimal processing)
- [x] Skip Office files by default, note in manifest
- [x] Filter trivial attachments (calendar invites, vcards, small images, generic filenames)
- [x] Combine thread + attachment content into deposit folder (PDFs deposited alongside content.md)

### Future (FastMCP v2)
- [ ] Async task dispatch for attachment extraction
- [ ] Return thread immediately, stream attachments as they complete
