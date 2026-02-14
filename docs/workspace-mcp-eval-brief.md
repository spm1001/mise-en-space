# Google Workspace MCP Evaluation Brief

Dense synthesis for evaluating a prototype MCP against patterns from 7 existing implementations.

---

## The 5 Critical Questions

Evaluate the prototype against these, in priority order:

### 1. Does it use native markdown export for Google Docs?

**What to look for:** `files.export` with `mimeType: "text/markdown"`

```typescript
// GOOD (only felores does this)
drive.files.export({ fileId, mimeType: "text/markdown" })

// BAD (taylorwilsdon, most others)
export_mime_type = {
    "application/vnd.google-apps.document": "text/plain",  // Loses all formatting
}
```

**Why it matters:** Google's server-side markdown conversion handles tables, headers, links. `text/plain` is a wall of unstructured text. Zero-dependency, single API call.

**Export MIME mapping (correct):**
| Google Type | Export As |
|-------------|-----------|
| Docs | `text/markdown` |
| Sheets | `text/csv` |
| Slides | `text/plain` (no markdown support) |
| Drawings | `image/png` |

---

### 2. Does it deposit files to disk and return paths?

**What to look for:** Working folder structure, path returns instead of inline content

```typescript
// GOOD (only aaronsb)
fs.writeFileSync(`${workspace}/${email}/downloads/${fileId}.md`, content)
return { path: `${workspace}/${email}/downloads/${fileId}.md` }

// BAD (everyone else)
return { content: documentText }  // Bloats context, can't grep
```

**Folder structure (aaronsb pattern):**
```
$WORKSPACE_BASE_PATH/
├── {email}/
│   ├── downloads/    # Fetched content
│   └── uploads/      # Staged for upload
└── shared/temp/      # Ephemeral
```

**Why it matters:** 50KB doc in context window vs on disk. File tools can slice. Binary content works. Persistent across operations.

---

### 3. Is discovery separate from content retrieval?

**What to look for:** `find_*` returns metadata only, `fetch_*` gets content

```typescript
// GOOD: Two-step pattern
find_files(query) → [{id, name, mimeType, modified, snippet}]  // No content
fetch_file(id) → {path}  // Content to disk

// BAD: Everything inline
search_files(query) → [{id, name, content: "full document..."}]  // Token bomb
```

**Why it matters:** Let caller decide what to fetch. 100 search results shouldn't mean 100 documents in context.

---

### 4. Does it use `fields` parameter for partial responses?

**What to look for:** `fields` parameter on list/get operations

```typescript
// GOOD (nobody does this consistently)
drive.files.list({
  q: query,
  fields: "files(id,name,mimeType,modifiedTime)"  // ~200 bytes/file
})

// BAD (everyone)
drive.files.list({ q: query })  // ~2KB/file with all metadata
```

**Why it matters:** 10x payload reduction on list operations. Free optimization, widely ignored.

---

### 5. Is multi-account first-class or bolted on?

**What to look for:** Account parameter on every tool, per-account credential storage

```python
# GOOD (mcp-gsuite pattern)
def query_gmail(__user_id__: str, query: str):  # Account required
    setup_oauth2(user_id=__user_id__)

# Also good (aaronsb pattern)
workspace/{email}/downloads/  # Folder isolation

# BAD
global_credentials.json  # Single account assumed
```

**Why it matters:** Work + personal accounts. Different orgs. Account context for AI ("use work account for ITV emails").

---

## Secondary Evaluation Criteria

### Pagination

| Pattern | Assessment |
|---------|------------|
| Opaque cursors (caller doesn't see tokens) | Best - MCP handles continuation |
| Token in response, re-call with `page_token` | Acceptable - explicit but works |
| No pagination | Bad - truncates results |

### Gmail: Thread vs Message

| Approach | When Right |
|----------|------------|
| Thread-first (`threads.list`) | Conversation context, replies |
| Message-first (`messages.list`) | Search, bulk operations |
| Both available | Ideal |

### Batch Operations

**Real batching:** Gmail `/batch` endpoint - 100 ops in 1 HTTP call
**Fake batching:** `Promise.all` on individual requests - still N API calls

Nobody uses real batching. If prototype claims batch, check if it's `/batch` or just parallel requests.

### Error Handling

Look for:
- API errors transformed to actionable guidance
- 403 `accessNotConfigured` → "Enable API at [console link]"
- Token refresh failures → "Re-authenticate with [tool]"
- Partial failure in batch → per-item status, not total failure

---

## Patterns Worth Stealing

### 1. Service Decorator (taylorwilsdon)
```python
@require_google_service("drive", "drive_read")
async def search_files(service, query):  # service auto-injected
```
Handles auth, caching (30min TTL), scopes declaratively.

### 2. Tool Tiers (taylorwilsdon)
```yaml
core: [search, get, create]
extended: [update, delete, permissions]
complete: [batch_*, admin_*]
```
`--tool-tier core` for constrained deployments. Progressive disclosure.

### 3. Filter Templates (GongRzhe)
```json
{"template": "fromSender", "parameters": {"email": "...", "labelIds": [...]}}
```
Encodes best practices. "Filter mailing list" not "construct filter JSON".

### 4. Account Metadata (mcp-gsuite)
```json
{"email": "work@company.com", "account_type": "work", "extra_info": "Has Team Calendar"}
```
AI knows which account for what purpose.

---

## Anti-Patterns to Flag

| Anti-Pattern | What It Looks Like | Why Bad |
|--------------|-------------------|---------|
| `text/plain` for Docs | `"document": "text/plain"` in export map | Loses all structure |
| Inline large content | `return { content: docText }` | Context bloat |
| Fake batch | `Promise.all(ids.map(id => api.get(id)))` | N API calls, not 1 |
| No `fields` | `files.list({ q })` without fields param | 10x payload waste |
| Single account | No `email` or `account` parameter | Can't do multi-account |
| Cloud function auth | External service holds client secret | Latency, privacy, dependency |

---

## API Capabilities Checklist

| Capability | Used By | Should Use |
|------------|---------|------------|
| `text/markdown` Drive export | felores only | Everyone |
| `fields` partial responses | Minimal | Everyone |
| Gmail `/batch` endpoint | Nobody | Bulk operations |
| `threads.*` for Gmail | taylorwilsdon, mcp-gsuite | Conversation context |
| Calendar `freebusy.query` | gemini-cli only | Scheduling |
| Docs `batchUpdate` | a-bonus only | Editing |
| Drive shortcut resolution | taylorwilsdon only | Shared Drive workflows |

---

## Ideal Tool Vocabulary

```
DISCOVERY (metadata only, no content)
├── find_emails(query, account?) → [{id, thread_id, subject, from, date, snippet}]
├── find_files(query, account?) → [{id, name, mime_type, modified}]
├── find_events(range, account?) → [{id, summary, start, end}]

FETCH (deposits to disk, returns path)
├── fetch_email(id, account?) → {path}
├── fetch_file(id, account?) → {path, mime_type}
├── fetch_thread(thread_id, account?) → {path}
├── fetch_attachment(msg_id, att_id, account?) → {path}

MUTATE (minimal surface)
├── send_email(to, subject, body, reply_to?, account?)
├── create_event(summary, start, end, attendees?, account?)
├── create_file(name, content, parent?, account?)
```

**Principles:**
- Discovery never writes files
- Fetch always writes files, returns paths
- Mutate is minimal (common operations only)
- Account optional, defaults to primary
- Pagination opaque (cursor managed internally)

---

## Quick Reference: Who Does What

| Implementation | Stars | Key Strength | Key Weakness |
|----------------|-------|--------------|--------------|
| taylorwilsdon | ~900 | 60+ tools, production-ready | `text/plain`, inline content |
| aaronsb | ~107 | **File deposit pattern** | Docker required, limited scope |
| felores | ~61 | **Native markdown export** | Only 2 tools |
| GongRzhe | ~817 | Filter templates | Fake batch, message-only |
| mcp-gsuite | ? | **Multi-account by design** | Gmail+Calendar only |
| a-bonus | ~241 | Deep Docs editing | Docs only |
| gemini-cli | ~98 | Policy engine | Cloud function auth |

---

## Evaluation Scoring

Rate prototype 0-2 on each:

| Criterion | 0 | 1 | 2 |
|-----------|---|---|---|
| Markdown export | `text/plain` | Has option | Default for Docs |
| File deposit | Inline only | Optional | Default pattern |
| Find/fetch split | Combined | Partial | Clean separation |
| `fields` usage | None | Some calls | Consistent |
| Multi-account | Single | Parameter exists | First-class |
| Pagination | None/exposed tokens | Token param | Opaque cursors |
| Error messages | Raw API errors | Transformed | Actionable guidance |

**10-14:** Strong foundation
**7-9:** Needs work on core patterns
**0-6:** Architectural rethink needed
