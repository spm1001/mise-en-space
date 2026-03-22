# Deep-dive code analysis of Google Workspace MCP implementations

**Native markdown export is the critical API capability nearly everyone is missing.** Only one implementation (felores/gdrive-mcp-server) uses Google Drive API’s native `text/markdown` export—all others use `text/plain` or HTML, requiring post-processing. The aaronsb implementation provides the only working folder pattern, which is essential for the target find/fetch/file architecture. No implementation uses Gmail’s native batch HTTP endpoint; all use application-level chunking instead.

## Executive summary

### Best implementation for each concern

|Concern                   |Best Implementation               |Why                                                 |
|--------------------------|----------------------------------|----------------------------------------------------|
|**Comprehensive coverage**|taylorwilsdon/google_workspace_mcp|60+ tools across 10 Google services                 |
|**File deposit pattern**  |aaronsb/google-workspace-mcp      |Only implementation with working folder architecture|
|**Markdown export**       |felores/gdrive-mcp-server         |Uses native `text/markdown` Drive API export        |
|**Gmail batch operations**|GongRzhe/Gmail-MCP-Server         |Application-level chunking with error isolation     |
|**Multi-account support** |MarkusPfundstein/mcp-gsuite       |True per-account credential isolation               |
|**Deep Docs editing**     |a-bonus/google-docs-mcp           |Full batchUpdate API access with 30+ tools          |
|**Official patterns**     |gemini-cli-extensions/workspace   |Privacy-focused local execution, policy engine      |

### Top 5 patterns to adopt

1. **Per-account workspace isolation** (aaronsb): `[email@domain.com]/downloads/` and `/uploads/` structure separates concerns and enables multi-account workflows
1. **Native markdown export via Drive API** (felores): `files.export` with `mimeType: "text/markdown"` eliminates conversion dependencies
1. **Service decorator injection** (taylorwilsdon): `@require_google_service("drive", "drive_read")` pattern handles auth, caching, and scope management declaratively 
1. **Filter template system** (GongRzhe): Pre-built templates like `fromSender`, `withSubject` abstract common Gmail automation patterns
1. **Tool tier organization** (taylorwilsdon): Core/Extended/Complete tiers enable progressive complexity and quota management 

### Top 3 anti-patterns to avoid

1. **Application-level batch “chunking”**: All implementations chunk at application layer (50 messages) rather than using Gmail’s native batch HTTP endpoint—wastes API quota
1. **Inline content return for large files**: Every implementation except aaronsb returns content inline, bloating context windows and preventing efficient file operations
1. **Single-format export**: Most implementations hardcode export formats rather than allowing caller choice—limits flexibility for downstream processing

### Critical API capabilities being missed

- **Gmail batch HTTP endpoint** (`/batch`): True batching reduces API calls by 50:1—none use it
- **Partial field responses** (`fields` parameter): Would reduce payload sizes significantly—minimal usage across implementations
- **Thread-first Gmail access**: Most use message-first approach, missing conversation context
- **Calendar free/busy queries**: None implement the bulk availability endpoint
- **Drive’s `text/markdown` export**: Only felores uses this; most use `text/plain`

-----

## Implementation profiles

### taylorwilsdon/google_workspace_mcp

**The comprehensive reference implementation with production-grade architecture.** 

|Attribute    |Value                      |
|-------------|---------------------------|
|Framework    |FastMCP 2.13.0+ (Python)   |
|Transport    |stdio, streamable-http, SSE|
|Tool count   |**60+** across 10 services |
|Lines of code|~8,000+ (estimate)         |

#### Architecture overview

The implementation uses a decorator-based service injection pattern that elegantly handles authentication, caching, and scope management:

```python
@require_google_service("drive", "drive_read")
async def search_drive_files(service, query: str, max_results: int = 10):
    """Service object auto-injected with 30-minute cache TTL"""
    result = service.files().list(q=query, pageSize=max_results).execute()
    return result
```

**Tool tier system** in `tool_tiers.yaml` enables progressive loading:

- `--tool-tier core`: Essential operations (search, get, create)
- `--tool-tier extended`: Core + management (delete, permissions)
- `--tool-tier complete`: All functionality (batch ops, comments)  

#### Strengths

- **Most comprehensive coverage**: Gmail, Drive, Docs, Sheets, Slides, Calendar, Forms, Tasks, Chat, Custom Search 
- **OAuth 2.1 multi-user support**: Bearer token authentication per request with `MCP_ENABLE_OAUTH21=true`  
- **Production deployment options**: Stateless container mode, Valkey/Redis session storage, Docker support 
- **Active maintenance**: Regular releases (v1.7.1 as of Dec 2025), responsive to issues

#### Weaknesses against target criteria

|Criterion            |Assessment                                                     |
|---------------------|---------------------------------------------------------------|
|Low latency          |⚠️ No partial field responses (`fields` parameter usage minimal)|
|Simple output formats|❌ Uses `text/plain` not `text/markdown` for Docs               |
|File deposit pattern |❌ All content returned inline—no filesystem working directory  |
|Full API breadth     |⚠️ Missing Gmail native batch, Calendar free/busy               |

#### Notable code patterns

**Credential store abstraction** enables pluggable storage backends:

```python
from auth.credential_store import get_credential_store
store = get_credential_store()  # LocalDirectory, Memory, Valkey
store.store_credential("user@example.com", credentials)
```

**Attachment serving via HTTP routes** (v1.6.1+):

```python
# Ephemeral ID handling with URL-based serving
serve_attachment(attachment_id)  # Returns HTTP URL, not base64
```

-----

### aaronsb/google-workspace-mcp

**The only implementation with explicit working folder pattern—critical for the target architecture.**

|Attribute     |Value                        |
|--------------|-----------------------------|
|Framework     |MCP TypeScript SDK direct    |
|Transport     |stdio only                   |
|Tool count    |**23 tools**                 |
|Key innovation|**File deposit architecture**|

#### WorkspaceManager class analysis

This is the critical differentiator. The `WorkspaceManager` implements per-account file isolation:

```
~/Documents/workspace-mcp-files/           # WORKSPACE_BASE_PATH (configurable)
├── [alice@company.com]/                   # Per-account isolation
│   ├── downloads/                         # Files from Drive API
│   └── uploads/                           # Files staged for upload
├── [bob@personal.com]/
│   ├── downloads/
│   └── uploads/
└── shared/temp/                           # Auto-cleanup temporary files
```

**Download flow** (API call → file write → path return):

```typescript
// download_drive_file tool
Input: { email: string, fileId: string, mimeType?: string }
→ Uses files.export for Google native files
→ Writes to: [email]/downloads/{filename}
→ Returns: Local file path (NOT content)
```

**Upload flow** (local file → Drive upload):

```typescript
// upload_drive_file tool
Input: { email: string, options: { name, content, mimeType, parents? } }
→ Reads from: [email]/uploads/
→ Uploads via Drive API
→ Returns: File metadata (id, webViewLink)
```

#### Strengths

|Strength                   |Impact                                         |
|---------------------------|-----------------------------------------------|
|**Large file handling**    |Content on disk, not in context window         |
|**Multi-account workflows**|Clear provenance via folder structure          |
|**Binary content support** |Images, PDFs work naturally                    |
|**Persistence**            |Downloaded files remain for multiple operations|

#### Weaknesses

- **Docker dependency**: Requires containerization for deployment
- **stdio only**: No HTTP/WebSocket transport options
- **No streaming**: Files fully downloaded before path returned
- **Limited documentation**: Export format handling not enumerated

#### Key pattern: path-based returns

```typescript
// Tool returns local path, NOT content
Result: "~/Documents/workspace-mcp-files/user@domain.com/downloads/document.pdf"

// Caller can then:
// 1. Read with filesystem tools
// 2. Process with other file-based tools
// 3. Use in subsequent upload operations
```

-----

### GongRzhe/Gmail-MCP-Server

**Correction: This is TypeScript/Node.js, not Python.** Specialized Gmail implementation with batch operations.

|Attribute  |Value                           |
|-----------|--------------------------------|
|Framework  |MCP TypeScript SDK              |
|Transport  |stdio, HTTP (port 3000), SSE    |
|Tool count |**18+ tools**                   |
|Key feature|Application-level batch chunking|

#### Batch operations analysis

**Critical finding**: Despite documentation claims, this does NOT use Gmail’s native batch HTTP endpoint. Instead, it uses application-level chunking:

```typescript
// batch_modify_emails implementation
{
  messageIds: ["182ab45cd67ef", ...],
  addLabelIds: ["IMPORTANT"],
  removeLabelIds: ["INBOX"],
  batchSize: 50  // Default, configurable
}
// Processes 50 messages at a time via individual API calls
// NOT using /batch endpoint for true batching
```

**Error isolation pattern** (worth adopting):

- Individual failures don’t stop batch processing 
- Detailed success/failure reporting  per message
- Automatic retry for failed items

#### Filter template system

Pre-built templates reduce configuration complexity: 

|Template         |Use Case                      |
|-----------------|------------------------------|
|`fromSender`     |Filter by sender email        |
|`withSubject`    |Filter by subject text        |
|`withAttachments`|Filter emails with attachments|
|`largeEmails`    |Filter by size threshold      |
|`containingText` |Filter by body content        |
|`mailingList`    |Filter mailing list emails    |

```json
{
  "template": "fromSender",
  "parameters": {
    "senderEmail": "notifications@github.com",
    "labelIds": ["Label_GitHub"],
    "archive": true
  }
}
```

#### Gmail API patterns

- **Message-first approach**: Uses `messages.list`, `messages.get` (no `threads.*`)
- **Format**: `format: 'full'` for complete MIME structure
- **Attachments**: Metadata inline, separate `download_attachment` tool for actual files 
- **Search**: Native Gmail `q` parameter with full query syntax support

-----

### felores/gdrive-mcp-server

**The implementation that correctly uses native markdown export—minimal but architecturally significant.**

|Attribute     |Value                            |
|--------------|---------------------------------|
|Framework     |MCP TypeScript SDK               |
|Transport     |stdio                            |
|Tool count    |**2 tools** only                 |
|Key innovation|**Native `text/markdown` export**|

#### Format conversion implementation (critical finding)

This is the ONLY implementation using Google Drive API’s native markdown export:

```typescript
switch (file.data.mimeType) {
  case "application/vnd.google-apps.document":
    exportMimeType = "text/markdown";  // KEY: Native markdown!
    break;
  case "application/vnd.google-apps.spreadsheet":
    exportMimeType = "text/csv";
    break;
  case "application/vnd.google-apps.presentation":
    exportMimeType = "text/plain";
    break;
  case "application/vnd.google-apps.drawing":
    exportMimeType = "image/png";
    break;
}

// Uses native API export—NO conversion libraries
const res = await drive.files.export(
  { fileId, mimeType: exportMimeType },
  { responseType: "text" }
);
```

**What this means**: Google’s server-side conversion handles complex document formatting (tables, headers, lists) consistently. No need for:

- ❌ pandoc
- ❌ markdownify/turndown
- ❌ Custom conversion logic

#### Export MIME types used

|Google File Type|Export MIME Type|
|----------------|----------------|
|Google Docs     |`text/markdown` |
|Google Sheets   |`text/csv`      |
|Google Slides   |`text/plain`    |
|Google Drawings |`image/png`     |

#### Limitations

- **Sheets**: CSV export returns only first sheet (Google API limitation)
- **Slides**: Plain text loses all formatting
- **No format selection**: Fixed mappings, user cannot choose
- **No file deposit**: Content returned inline

-----

### gemini-cli-extensions/workspace

**Official Google reference implementation—minimal but demonstrates best practices.**

|Attribute  |Value                                    |
|-----------|-----------------------------------------|
|Framework  |MCP TypeScript SDK (standard, not custom)|
|Transport  |stdio                                    |
|Tool count |Minimal/curated selection                |
|Key feature|**Policy engine for tool permissions**   |

#### Design philosophy

Intentionally minimal, covering “primary modalities of knowledge work”:

- File Management (Drive)
- Document Authoring (Docs)
- Time Management (Calendar)
- Communication (Gmail, Chat) 

**Why minimal?**

1. Started as “starter project” for DeepMind employee
1. Security: Fewer tools = smaller attack surface
1. Extensibility: Template for community additions
1. Quick adoption: Simple installation

#### Policy engine (worth adopting)

User-configurable permissions in `~/.gemini/policy.toml`:

```toml
[[rule]]
toolName = "gmail_send_email"
decision = "ask_user"
priority = 100

[[rule]]
toolName = "drive_delete_file"
decision = "deny"
priority = 999
```

#### Official patterns

|Pattern          |Description                                           |
|-----------------|------------------------------------------------------|
|Snake_case naming|`gmail_send_email`, `drive_search`                    |
|Local execution  |Privacy-preserving, no third-party intermediary       |
|OAuth-first      |No API key management                                 |
|Context files    |`GEMINI.md`, `WORKSPACE-Context.md` for model guidance|

-----

### a-bonus/google-docs-mcp

**Deepest Docs API access with full batchUpdate capabilities.**

|Attribute  |Value                          |
|-----------|-------------------------------|
|Framework  |FastMCP (TypeScript)           |
|Tool count |**30+ tools**                  |
|Key feature|**Deep batchUpdate API access**|

#### Docs API batchUpdate usage

```typescript
const MAX_BATCH_UPDATE_REQUESTS = 50;

// Request types implemented:
- insertText (at index)
- deleteContentRange
- updateTextStyle (bold, italic, colors, fonts)
- updateParagraphStyle (alignment, spacing)
- insertTable (rows, columns)
- insertPageBreak
- insertInlineImage
```

#### Document structure handling

- **Index-based positioning**: All operations use 1-based character index
- **Tab support**: Optional `tabId` parameter for multi-tab documents
- **Range operations**: Start/end index for formatting
- **Helper functions**: `findTextRange`, `getParagraphRange` for navigation

-----

### MarkusPfundstein/mcp-gsuite

**True multi-account support via credential isolation.**

|Attribute  |Value                               |
|-----------|------------------------------------|
|Framework  |Python MCP SDK (native)             |
|Tool count |~12 tools (Gmail + Calendar)        |
|Key feature|**Per-account credential isolation**|

#### Multi-account implementation

**`.accounts.json`** registry:

```json
{
  "accounts": [
    {
      "email": "alice@company.com",
      "account_type": "work",
      "extra_info": "Primary work account - has Team Calendar"
    },
    {
      "email": "bob@personal.com",
      "account_type": "personal",
      "extra_info": "Contains Family Calendar"
    }
  ]
}
```

**Token management**: Each account gets own credential file: `.oauth.{email}.json` 

**Account switching**: No explicit switch—tools accept `email` parameter, AI queries different accounts in same conversation.

-----

## Comparative analysis

### Framework comparison table

|Implementation                    |Framework       |Transports      |Tool Count|LOC (est.)|
|----------------------------------|----------------|----------------|----------|----------|
|taylorwilsdon/google_workspace_mcp|FastMCP (Python)|stdio, HTTP, SSE|60+       |~8,000    |
|aaronsb/google-workspace-mcp      |MCP TS SDK      |stdio           |23        |~3,000    |
|GongRzhe/Gmail-MCP-Server         |MCP TS SDK      |stdio, HTTP, SSE|18+       |~2,500    |
|felores/gdrive-mcp-server         |MCP TS SDK      |stdio           |2         |~300      |
|gemini-cli-extensions/workspace   |MCP TS SDK      |stdio           |~15       |~1,500    |
|a-bonus/google-docs-mcp           |FastMCP (TS)    |stdio           |30+       |~4,000    |
|MarkusPfundstein/mcp-gsuite       |Python SDK      |stdio           |~12       |~2,000    |

### Tool abstraction spectrum

```
Thin API Wrapper ←―――――――――――――――→ Workflow-Shaped ←―――――――――――――――→ Oracle
       │                                   │                            │
   felores                          taylorwilsdon                  (none exist)
   gemini-cli                       aaronsb
                                    GongRzhe
                                    a-bonus
```

**Assessment**:

- **Thin wrappers**: felores (2 tools, near-direct API mapping), gemini-cli (curated but 1:1)
- **Workflow-shaped**: taylorwilsdon (tiers, helpers), aaronsb (file deposit flow), GongRzhe (filter templates)
- **Oracle**: None implement natural language → API translation; all require structured parameters

### API usage matrix

|Capability                  |taylorwilsdon|aaronsb  |gemini-cli|GongRzhe|felores|a-bonus|
|----------------------------|:-----------:|:-------:|:--------:|:------:|:-----:|:-----:|
|Drive `text/markdown` export|❌            |❌        |❓         |N/A     |✅      |❌      |
|Gmail batch HTTP endpoint   |❌            |N/A      |❌         |❌       |N/A    |N/A    |
|Thread-first Gmail          |⚠️ both       |N/A      |❓         |❌       |N/A    |N/A    |
|Partial field responses     |⚠️ minimal    |✅        |❓         |❌       |❌      |❌      |
|Calendar free/busy          |❌            |❌        |❓         |N/A     |N/A    |N/A    |
|Docs batchUpdate            |✅            |N/A      |❓         |N/A     |N/A    |✅      |
|Multi-account support       |✅ OAuth 2.1  |✅ folders|❌         |❌       |❌      |❌      |

### Content delivery comparison

|Implementation|Inline vs File  |Truncation         |Caching          |
|--------------|----------------|-------------------|-----------------|
|taylorwilsdon |Inline          |None (API limits)  |Service 30min TTL|
|aaronsb       |**File deposit**|N/A (files on disk)|None             |
|GongRzhe      |Inline          |None documented    |None             |
|felores       |Inline          |None               |None             |
|gemini-cli    |Inline          |None documented    |None             |
|a-bonus       |Inline          |None               |None             |

-----

## Pattern catalog

### Pattern 1: Service decorator injection

**Implementations**: taylorwilsdon

```python
@require_google_service("drive", "drive_read")
async def search_drive_files(service, query: str):
    # service auto-injected, cached, scoped
    return service.files().list(q=query).execute()
```

**Why it matters**: Eliminates authentication boilerplate from every tool, centralizes scope management, enables caching.

### Pattern 2: Per-account workspace folders

**Implementation**: aaronsb

```
[email@domain.com]/
├── downloads/
└── uploads/
```

**Why it matters**: Essential for file deposit architecture, enables multi-account workflows with clear provenance, prevents cross-contamination.

### Pattern 3: Native markdown export

**Implementation**: felores

```typescript
drive.files.export({ fileId, mimeType: "text/markdown" })
```

**Why it matters**: Eliminates conversion dependencies, leverages Google’s maintained conversion, ensures consistency.

### Pattern 4: Error isolation in batch operations

**Implementation**: GongRzhe

```typescript
// Individual failures don't stop batch
// Detailed per-message success/failure
// Automatic retry for failed items
```

**Why it matters**: Partial success is often acceptable; total failure due to one bad item is frustrating.

### Pattern 5: Filter templates

**Implementation**: GongRzhe

```json
{ "template": "fromSender", "parameters": { "senderEmail": "...", "labelIds": [...] } }
```

**Why it matters**: Abstracts common patterns, reduces configuration complexity, provides discoverable workflows.

### Pattern 6: Tool tier organization

**Implementation**: taylorwilsdon

```yaml
core: [search_drive_files, get_drive_file_content, ...]
extended: [list_drive_items, update_drive_file, ...]
complete: [get_drive_file_permissions, ...]
```

**Why it matters**: Progressive disclosure, quota management, reduced cognitive load.

-----

## Anti-pattern catalog

### Anti-pattern 1: Application-level batch “chunking”

**Implementations**: All (GongRzhe most explicitly)

**What’s wrong**: Processing 50 messages in a loop makes 50 API calls. Gmail’s batch endpoint allows 100 requests in 1 HTTP call.

**Better alternative**: Use `/batch` multipart request:

```http
POST https://www.googleapis.com/batch/gmail/v1
Content-Type: multipart/mixed; boundary=batch_boundary

--batch_boundary
Content-Type: application/http
GET /gmail/v1/users/me/messages/id1

--batch_boundary
Content-Type: application/http
GET /gmail/v1/users/me/messages/id2
...
```

### Anti-pattern 2: Inline content for large files

**Implementations**: All except aaronsb

**What’s wrong**: A 50KB document consumes context window space, can’t be processed with file tools, breaks on binary content.

**Better alternative**: Write to filesystem, return path:

```typescript
// Bad
return { content: documentText }

// Good  
fs.writeFileSync(path, content)
return { path: path }
```

### Anti-pattern 3: Ignoring `fields` parameter

**Implementations**: Most

**What’s wrong**: Default responses include all fields; a file list returns full metadata when only names/IDs needed.

**Better alternative**:

```typescript
drive.files.list({
  q: query,
  fields: "files(id,name,mimeType)"  // Only what's needed
})
```

-----

## Hidden API capabilities

### Used cleverly by one implementation

|Capability            |Implementation|Impact                        |
|----------------------|--------------|------------------------------|
|Native markdown export|felores       |Zero-dependency conversion    |
|Per-account folders   |aaronsb       |Multi-account isolation       |
|Service caching       |taylorwilsdon |30-min auth overhead reduction|

### Not used by any but should be

|Capability                          |What it does                |Relevance                     |
|------------------------------------|----------------------------|------------------------------|
|Gmail batch HTTP endpoint           |100 operations per HTTP call|Critical for bulk operations  |
|Calendar `freebusy.query`           |Bulk availability check     |Meeting scheduling workflows  |
|Drive `changes.watch`               |Push notifications          |Real-time sync without polling|
|Docs `suggestionsViewMode`          |Access to suggestions       |Collaboration workflows       |
|Drive `copyRequiresWriterPermission`|Prevent unauthorized copying|Security-sensitive sharing    |

### Particularly relevant to target criteria

|API Feature                  |Why Relevant              |Current Usage|
|-----------------------------|--------------------------|-------------|
|`text/markdown` export       |Low latency, simple output|Only felores |
|`fields` parameter           |Low latency               |Minimal      |
|Gmail batch endpoint         |Low latency               |None         |
|`files.export` vs `files.get`|Correct operation per type|All correct  |

-----

## Recommendations

### Proposed tool vocabulary

Building on find/fetch/file pattern:

```
find_*          # Discovery, returns metadata only
  find_drive_files(query) → [{id, name, mimeType, modified}]
  find_gmail_threads(query) → [{threadId, subject, snippet, date}]
  find_calendar_events(range) → [{eventId, summary, start, end}]

fetch_*         # Retrieval, deposits to working folder
  fetch_drive_file(fileId, format?) → {path: "/workspace/file.md"}
  fetch_gmail_thread(threadId) → {path: "/workspace/thread.txt"}
  fetch_calendar_event(eventId) → {path: "/workspace/event.json"}

action_*        # Mutations, minimal use
  send_gmail(to, subject, body, attachments?)
  create_calendar_event(summary, start, end, attendees?)
  create_drive_file(name, content, parent?)
```

**Key principles**:

1. Metadata-first discovery (find returns IDs + preview)
1. Explicit fetch with file deposit
1. Caller uses file tools to slice content
1. Pagination opaque (cursors managed internally)

### Recommended framework choice

**Recommendation: FastMCP (Python)** with MCP TypeScript SDK as alternative. 

|Factor            |FastMCP (Python)       |MCP TS SDK        |
|------------------|-----------------------|------------------|
|Developer velocity|✅ Decorator patterns   |⚠️ More boilerplate|
|Performance       |✅ Async native, caching|✅ Async native    |
|Type safety       |⚠️ Runtime only         |✅ Compile-time    |
|Deployment        |✅ uvx, pip, Docker     |✅ npm, Docker     |
|Ecosystem         |✅ googleapis, rich libs|✅ googleapis      |

**Rationale**: FastMCP’s decorator patterns (`@require_google_service`) provide the cleanest abstraction for Google API auth.  Python’s ecosystem has more mature Google API tooling.

### API usage recommendations

1. **Always use `text/markdown` export for Docs** (felores pattern)
1. **Implement true Gmail batch** via `/batch` endpoint
1. **Use `fields` parameter** for all list operations
1. **Prefer thread-first Gmail** for conversation context
1. **Implement Calendar free/busy** for scheduling workflows

### File deposit architecture proposal

```
~/.mcp-workspace/                    # Configurable via env var
├── config/
│   └── accounts.json               # Multi-account registry
├── [account@domain.com]/
│   ├── drive/
│   │   ├── {fileId}.md             # Fetched files by ID
│   │   └── index.json              # Local metadata cache
│   ├── gmail/
│   │   ├── {threadId}.txt          # Fetched threads
│   │   └── attachments/            # Downloaded attachments
│   └── calendar/
│       └── {eventId}.json          # Fetched events
└── temp/                           # Auto-cleanup staging
```

**Key behaviors**:

1. `find_*` tools never write files
1. `fetch_*` tools always write to account folder, return path
1. Filenames use IDs for deduplication
1. Metadata cache enables offline queries
1. `temp/` cleaned on server start

-----

## Conclusion

The existing Google Workspace MCP landscape reveals a critical gap: **no implementation combines comprehensive API coverage with a file deposit architecture optimized for LLM workflows**. The closest alignment with target criteria comes from combining:

- **aaronsb’s working folder pattern** for file deposit
- **felores’s native markdown export** for simple output
- **taylorwilsdon’s service decorator pattern** for clean auth 
- **GongRzhe’s filter templates** for workflow abstraction

The most impactful improvement opportunity is implementing **true Gmail batch operations** using the `/batch` endpoint—this alone could reduce API calls by **50:1** for bulk operations. Combined with consistent use of the `fields` parameter and `text/markdown` export, a next-generation MCP could achieve significantly lower latency than any existing implementation while maintaining the comprehensive coverage of taylorwilsdon’s approach.