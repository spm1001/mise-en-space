# Email Attachment Exfiltration

Apps Script that extracts email attachments to dated Drive folders for unified search.

## What It Does

- Searches Gmail for emails with attachments
- Filters out trivial files (small images, calendar invites, vcards)
- Uploads meaningful attachments to `Email Attachments/YYYY-MM/` folders in Drive
- Creates shortcuts for Drive/Docs links mentioned in email bodies
- Enables Drive fullText search to find content inside extracted PDFs

## Deployment Model

**Each user deploys their own copy.** This means:
- Your own Apps Script project
- Your own OAuth authorization
- Files go to YOUR My Drive (not shared)
- You can customize filters for your email patterns

## Setup

### 1. Create Apps Script Project

1. Go to [script.google.com](https://script.google.com)
2. Create new project → name it "Email Attachment Exfiltration"
3. Copy the script ID from the URL: `https://script.google.com/d/{SCRIPT_ID}/edit`

### 2. Enable the manifest

In your new project: **Project Settings** (gear icon) → check **"Show appsscript.json manifest file in editor"**. This lets step 3 replace the default manifest.

### 3. Copy Code Files

Copy these files from `src/` to your Apps Script project:
- `Code.gs` — main logic
- `Config.gs` — user settings (folder name, exclusions, backfill years)
- `FilterConfig.gs` — attachment filter patterns
- `appsscript.json` — manifest with required scopes and advanced services (replace the default)

**Edit `Config.gs` for your setup:**
- `BACKFILL_YEARS` — set to the years you want to process
- `EXCLUDED_SUBJECT_PATTERNS` — add newsletters and automated emails you receive
- `ROOT_FOLDER_NAME` — leave as `'Email Attachments'` if you use mise search (see [Integration](#integration-with-mise-en-space))

### 4. First Run

Run `testBackfillDryRun` from the editor to trigger OAuth consent. Authorize the requested scopes (Gmail read, Drive read/write, ScriptApp for triggers).

If you get an "Advanced service not enabled" error: in the editor, go to **Services** (+ icon in left sidebar) and enable **Gmail API** and **Drive API**.

### 5. Set Up Triggers

**For backfill (historical emails):**
1. Run `setupTriggers()` from the editor
2. This creates a 15-minute trigger that processes all years in `BACKFILL_YEARS`
3. Monitor progress with `viewCheckpoints()`

**For ongoing processing (after backfill completes):**
1. Run `setupOngoingTrigger()` from the editor
2. This creates a 15-minute trigger for `processNewEmails`

### 6. Optional: `itv-appscript` deployment

If you have `itv-appscript` (the Apps Script deploy CLI), copy `deploy.json.example` to `deploy.json` and fill in your script ID and GCP project:

```bash
cp deploy.json.example deploy.json
# Edit deploy.json with your scriptId and gcpProjectId
itv-appscript deploy
```

This is optional — manual copy-paste into the Apps Script editor works fine.

## Folder Structure

```
My Drive/
└── Email Attachments/
    ├── 2024-01/
    │   ├── report.pdf
    │   ├── data.xlsx
    │   └── Link-1a2b3c4d5e6f  (shortcut to Docs link)
    ├── 2024-02/
    └── ...
```

## Filtering

Attachments are automatically skipped if they match:
- Calendar invites (`.ics`)
- Contact cards (`.vcf`)
- GIFs (often animated signatures)
- Images smaller than 200KB (logos, signatures, inline graphics)
- Generic filenames (`image.png`, `photo.jpg`, `attachment.pdf`, etc.)

Edit `EXCLUDED_SUBJECT_PATTERNS` in Config.gs to skip emails by subject.

Filter patterns are shared with the Python MCP server via `config/attachment_filters.json` in the repo root. If you don't have `itv-appscript`, edit `FilterConfig.gs` directly.

## State Management

**Drive-based dedup:** File descriptions contain `Message ID` and `Content Hash`. The script scans existing files to avoid reprocessing.

**Checkpoints:** Month/offset progress stored in Script Properties. View with `viewCheckpoints()`.

**In-flight IDs:** Messages processed but filtered (no file uploaded) are tracked in Properties to avoid reprocessing.

## Useful Functions

| Function | Purpose |
|----------|---------|
| `testBackfillDryRun()` | Preview what would be processed (last 30 days) |
| `testBackfill()` | Process last 30 days for real |
| `viewStats()` | Count files in Drive folders |
| `viewCheckpoints()` | Show backfill progress |
| `listTriggers()` | List active triggers |
| `clearTriggers()` | Remove all triggers |
| `chunkYear(year)` | Manually process a chunk for a specific year |
| `resetCheckpoint(year)` | Restart a year from month 1 |
| `resetOffset(year)` | Re-scan current month only |

## Monitoring & Notifications

**Automatic failure emails:** Google emails you when triggers fail — from `noreply-apps-scripts-notifications@google.com`. No setup required.

**Execution logs:**
- Script editor → Executions (left sidebar)
- GCP Console → Logging (if linked to GCP project)

## Troubleshooting

### "Exceeded maximum execution time"
The chunk functions are designed for 15-minute intervals. If hitting limits:
- Reduce `CHUNK_SIZE` in Config.gs (default: 50)
- Check for pathological months with huge attachment counts

### "Advanced service not enabled"
Go to **Services** (+) in the editor sidebar → enable **Gmail API** and **Drive API**.

### Duplicate folders created
Rare race condition if multiple triggers fire simultaneously. Manually merge folders and delete duplicates.

### Files not being filtered
Check `isTrivialAttachment()` and `EXCLUDED_SUBJECT_PATTERNS`. Run `testBackfillDryRun()` to see what would be processed.

### Re-running after errors
- `resetCheckpoint(2024)` — restart year from month 1
- `resetOffset(2025)` — re-scan current month only
- `resetState()` — nuclear option, clears everything

## Adding a New Year

Add the year to `BACKFILL_YEARS` in Config.gs:

```javascript
const BACKFILL_YEARS = [2023, 2024, 2025, 2026, 2027];
```

No other changes needed — all checkpoint, trigger, and stats functions read from this array.

## Integration with mise-en-space

The mise MCP server detects pre-extracted attachments via Drive search:
- Drive fullText search indexes PDF content, making email attachments searchable
- This is the "pre-exfil detection" pattern documented in the main CLAUDE.md

**Important:** mise auto-discovers the folder by searching for one named `Email Attachments` in Drive. If you rename `ROOT_FOLDER_NAME` in Config.gs, set the `MISE_EMAIL_ATTACHMENTS_FOLDER_ID` env var to the folder's Drive ID so mise can still find it.
