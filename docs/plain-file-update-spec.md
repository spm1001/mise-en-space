# Spec: mise `do` operations on plain Drive files

## Problem

`mise do` operations (`overwrite`, `replace_text`, `prepend`, `append`) all route through the Google Docs API (`docs.googleapis.com/v1/documents/{id}`). This works for Google Docs (`application/vnd.google-apps.document`) but returns HTTP 400 for plain files stored in Drive (e.g. `text/markdown`, `text/plain`, `application/json`).

This is a real gap. The ITV wiki pipeline stores markdown files as plain `text/markdown` in Drive. A Cloud Function syncs them to GitHub. When we found a content bug (duplicated bullet list + name typo in `intro.md`), we could fix it in git via PR but couldn't update the Drive source through mise — forcing a manual edit in the Drive UI.

### Error observed

```
mise do replace_text on file 1ghHI6EoxnHWyFaXHvrSkMHDbz-ismeUq (text/markdown)

→ HttpError 400: "Request contains an invalid argument."
  URL: https://docs.googleapis.com/v1/documents/1ghHI6EoxnHWyFaXHvrSkMHDbz-ismeUq
```

The Docs API correctly rejects the request — it's not a Google Doc.

## Solution

Detect the file's mime type before choosing the API path. Google Docs get the Docs API (existing behavior). Everything else gets the Drive Files API for content operations.

### Detection

```python
# Already available from drive.files().get()
metadata = service.files().get(fileId=file_id, fields="mimeType,name").execute()
is_google_doc = metadata["mimeType"] == "application/vnd.google-apps.document"
```

This check should happen once at the start of any `do` operation that touches content (`overwrite`, `replace_text`, `prepend`, `append`). Non-content operations (`move`, `rename`, `share`) don't need it — they use the Drive API already.

### API paths by operation

| Operation | Google Doc (existing) | Plain file (new) |
|-----------|----------------------|------------------|
| `overwrite` | Docs API: clear body + insert | Drive API: `files().update(media_body=...)` |
| `replace_text` | Docs API: `replaceAllText` | Download → string replace → re-upload |
| `prepend` | Docs API: insert at index 1 | Download → prepend → re-upload |
| `append` | Docs API: insert at end index | Download → append → re-upload |

### Drive Files API for content update

```python
from googleapiclient.http import MediaInMemoryUpload

# Download current content
content = service.files().get_media(fileId=file_id).execute().decode("utf-8")

# Modify content (example: replace_text)
new_content = content.replace(find_text, replace_text)

# Upload modified content
media = MediaInMemoryUpload(
    new_content.encode("utf-8"),
    mimetype=metadata["mimeType"],  # preserve original mime type
    resumable=False
)
result = service.files().update(
    fileId=file_id,
    media_body=media
).execute()
```

For `overwrite` with `source=` (deposit folder), read `content.md` from the deposit and upload directly — same as today's logic but via Drive API instead of Docs API.

For `overwrite` with `content=` (inline), upload the content string directly.

### Operation details

#### `overwrite` (plain file)

```python
if source:
    # Read from deposit folder (existing logic for resolving source)
    content_path = Path(source) / "content.md"  # or content.csv, etc.
    file_content = content_path.read_bytes()
    mime = metadata["mimeType"]
else:
    # Inline content
    file_content = content.encode("utf-8")
    mime = metadata["mimeType"]

media = MediaInMemoryUpload(file_content, mimetype=mime, resumable=False)
service.files().update(fileId=file_id, media_body=media).execute()
```

No markdown → Docs heading conversion (that's Google Docs specific). Plain file overwrite is a raw byte replacement.

**Cues:** `{"char_count": len(file_content), "mime_type": mime, "plain_file": true}`

#### `replace_text` (plain file)

```python
content = service.files().get_media(fileId=file_id).execute().decode("utf-8")
count = content.count(find_text)
if count == 0:
    return {"warning": "Text not found", "occurrences_changed": 0}

new_content = content.replace(find_text, replace_text)
media = MediaInMemoryUpload(new_content.encode("utf-8"), mimetype=metadata["mimeType"])
service.files().update(fileId=file_id, media_body=media).execute()
```

**Cues:** `{"occurrences_changed": count, "plain_file": true}`

#### `prepend` (plain file)

```python
existing = service.files().get_media(fileId=file_id).execute().decode("utf-8")
new_content = content + existing
# upload...
```

#### `append` (plain file)

```python
existing = service.files().get_media(fileId=file_id).execute().decode("utf-8")
new_content = existing + content
# upload...
```

### Implementation location

The routing belongs in `do_handler()` (or whatever the top-level dispatch function is). Pseudocode:

```python
async def do_handler(operation, file_id, **kwargs):
    if operation in ("overwrite", "replace_text", "prepend", "append"):
        metadata = drive_service.files().get(fileId=file_id, fields="mimeType,name").execute()

        if metadata["mimeType"] == "application/vnd.google-apps.document":
            return await do_google_doc_operation(operation, file_id, metadata, **kwargs)
        else:
            return await do_plain_file_operation(operation, file_id, metadata, **kwargs)
    else:
        # move, rename, share — unchanged
        return await do_drive_operation(operation, file_id, **kwargs)
```

### Mime types to handle

All non-Google-Doc mime types go through the plain file path. Common ones we'd expect:

| Mime type | Example |
|-----------|---------|
| `text/markdown` | `.md` files (the case that triggered this) |
| `text/plain` | `.txt` files |
| `text/csv` | `.csv` files |
| `application/json` | `.json` config files |
| `text/html` | `.html` files |
| `application/javascript` | `.js` files |
| `text/yaml` | `.yaml`/`.yml` files |

Binary files (`image/*`, `application/pdf`, etc.) should be rejected for `replace_text`, `prepend`, `append` with a clear error: "Text operations not supported on binary files. Use overwrite for full replacement." `overwrite` on binary files should work (raw byte replacement).

### Binary detection

```python
is_text = metadata["mimeType"].startswith("text/") or metadata["mimeType"] in (
    "application/json", "application/javascript", "application/xml",
    "application/x-yaml", "application/toml"
)
```

### Edge cases

1. **Encoding**: Assume UTF-8. If `decode("utf-8")` fails, try `latin-1` as fallback, then error with `"File encoding not supported for text operations"`.

2. **Large files**: Drive API `get_media()` loads entire file into memory. For files >10MB, this is risky. Add a size check via `files().get(fields="size")` and warn if >5MB: `"File is {size}MB — text operations on large files may be slow"`.

3. **Race conditions**: Between download and re-upload, someone else could edit. This is inherent to the download-modify-upload pattern. Not worth solving — same risk as editing in the Drive UI. Could note in cues: `"modified_time": metadata["modifiedTime"]` for auditability.

4. **Google Sheets/Slides**: `application/vnd.google-apps.spreadsheet` and `application/vnd.google-apps.presentation` should still route through their respective APIs (Sheets API, Slides API) — not the plain file path. The check should be:

```python
GOOGLE_NATIVE_TYPES = {
    "application/vnd.google-apps.document",      # → Docs API (existing)
    "application/vnd.google-apps.spreadsheet",    # → Sheets API (existing)
    "application/vnd.google-apps.presentation",   # → Slides API (existing)
}

if metadata["mimeType"] in GOOGLE_NATIVE_TYPES:
    return await do_native_operation(...)
else:
    return await do_plain_file_operation(...)
```

5. **`source=` with wrong file in deposit**: If `overwrite` uses `source=` and the deposit has `content.md` but the target is a `.json` file, upload `content.md` contents anyway (the user explicitly asked). Don't try to match deposit file extensions to target mime types.

## Skill update

After implementation, update the mise SKILL.md:

- Remove the note about `mise do` not working on plain markdown files
- Add to the "Choosing the Right Edit Operation" table: "Plain files in Drive (markdown, JSON, etc.) — all edit operations work, uses Drive Files API under the hood"
- No new verbs or flags needed — existing interface is sufficient

## Testing

1. **Happy path**: `replace_text` on a `text/markdown` file in Drive
2. **Overwrite**: `overwrite` on a `text/markdown` file with `content=` and with `source=`
3. **Prepend/append**: on a plain text file
4. **Google Doc unchanged**: verify existing Docs API path still works
5. **Binary rejection**: `replace_text` on a PDF → clear error
6. **Large file warning**: file >5MB triggers warning in cues
7. **Not found text**: `replace_text` where find text doesn't exist → `occurrences_changed: 0`

## Motivation (for the commit message)

When content pipelines use plain markdown files in Drive (not Google Docs), Claude can read them via `mise fetch` but can't edit them via `mise do`. This forces manual Drive UI edits — breaking the automation loop. Adding plain file support to `do` operations closes this gap with zero interface changes.
