# Google Workspace MCP Implementation Analysis

Deep-dive code analysis completed 2026-01-17. All repositories cloned and traced.

---

## Executive Summary

### Best Implementation for Each Concern

| Concern | Winner | Why |
|---------|--------|-----|
| **Native markdown export** | felores/gdrive-mcp-server | Only one correctly using Drive's `text/markdown` export MIME |
| **File deposit pattern** | aaronsb/google-workspace-mcp | Full WorkspaceManager with per-account directories |
| **Multi-account** | MarkusPfundstein/mcp-gsuite | Every tool takes `__user_id__`, purpose-built |
| **Gmail batch** | taylorwilsdon/google_workspace_mcp | Proper batching with SSL retry logic (GongRzhe's is fake) |
| **Docs editing** | a-bonus/google-docs-mcp | Full `batchUpdate` support, deep document structure |
| **Tool breadth** | gemini-cli-extensions/workspace | 47 tools across 10 services |
| **Production hardening** | taylorwilsdon/google_workspace_mcp | Error handling, tier system, session security |

### Top 5 Patterns to Steal

1. **Native markdown export** (felores) - Google Docs export to `text/markdown` is a single API call. Most implementations miss this.

2. **Working folder deposit** (aaronsb) - `WorkspaceManager` class with `$WORKSPACE_BASE_PATH/{email}/downloads/` structure. Tools return `filePath` not content.

3. **Tool tiers** (taylorwilsdon) - YAML-defined core/extended/complete tiers allow progressive disclosure. CLI flag `--tool-tier` filters available tools.

4. **Filter templates** (GongRzhe) - Pre-built Gmail filter patterns (`fromSender`, `mailingList`, `largeEmails`) with parameter substitution.

5. **Tab-aware Docs** (a-bonus, gemini-cli) - Multi-tab Google Docs support with `tabId` parameter on all operations.

### Top 3 Anti-Patterns to Avoid

1. **Fake batching** (GongRzhe) - Claims "50/batch" but uses `Promise.all` on individual requests, not Gmail's native batch endpoints. Rate limits hit faster, no atomicity.

2. **text/plain for Docs** (taylorwilsdon) - Exports Google Docs as `text/plain` instead of `text/markdown`. Loses all formatting, links, structure.

3. **Cloud function auth** (gemini-cli) - Requires external cloud function to hold client secret. Not self-contained, introduces latency, privacy concerns.

### Critical API Capabilities Being Missed

| Capability | Who Uses It | Who Should |
|------------|-------------|------------|
| `text/markdown` Drive export | felores, aaronsb only | Everyone |
| `fields` partial responses | Nobody | Everyone (reduces payload) |
| Gmail `users.messages.batchModify` | Nobody | GongRzhe (claims batch) |
| Gmail `format=metadata` for search | GongRzhe only | All Gmail tools |
| Docs `batchUpdate` | a-bonus only | Anyone doing edits |
| Calendar free/busy | gemini-cli only | Calendar-heavy tools |

---

## Framework Comparison Table

| Implementation | Framework | Language | Transport | Tool Count | LoC (core) |
|----------------|-----------|----------|-----------|------------|------------|
| taylorwilsdon/google_workspace_mcp | FastMCP (SecureFastMCP subclass) | Python | stdio | ~35 | ~4000 |
| aaronsb/google-workspace-mcp | @modelcontextprotocol/sdk 0.7 | TypeScript | stdio | 26 | ~2500 |
| gemini-cli-extensions/workspace | @modelcontextprotocol/sdk | TypeScript | stdio | 47 | ~3000 |
| GongRzhe/Gmail-MCP-Server | @modelcontextprotocol/sdk 0.4 | TypeScript | stdio | 19 | ~1200 |
| a-bonus/google-docs-mcp | FastMCP (TS) | TypeScript | stdio | 31 | ~1500 |
| felores/gdrive-mcp-server | @modelcontextprotocol/sdk 1.0 | TypeScript | stdio | 2 | ~200 |
| MarkusPfundstein/mcp-gsuite | mcp.server (Python) | Python | stdio | 12 | ~800 |

**Observations:**
- FastMCP used by 2 implementations (Python and TS versions differ)
- All use stdio transport - no SSE or HTTP implementations found
- Tool count varies 2-47 - design philosophy, not capability

---

## Tool Abstraction Spectrum

```
Thin API Wrapper ←————————————————————————————————→ Workflow-Shaped
       │                    │                              │
   felores              aaronsb                    mcp-gsuite
   (2 tools,          (26 tools,                  (12 tools,
    pure export)       file deposit)               multi-account
                                                   email threading)
                           │
                    taylorwilsdon
                    (35 tools, tiered,
                     but still 1:1 mapping)
                           │
                      gemini-cli
                      (47 tools, redirects
                       between tools)
                           │
                    google-docs-mcp
                    (31 tools, text finding,
                     markdown conversion)
```

**No true "Oracle" implementations** - None attempt natural language → API translation.

---

## API Usage Matrix

| Capability | taylorwilsdon | aaronsb | gemini-cli | GongRzhe | felores | a-bonus | mcp-gsuite |
|------------|:-------------:|:-------:|:----------:|:--------:|:-------:|:-------:|:----------:|
| **Drive** ||||||||
| Markdown export (`text/markdown`) | ❌ | ✅ | ❌ | — | ✅ | — | — |
| `fields` partial responses | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Shared Drives support | ✅ | ✅ | ✅ | — | ✅ | ❌ | — |
| Shortcut resolution | ✅ | ❌ | ❌ | — | ❌ | ❌ | — |
| **Gmail** ||||||||
| Thread-first operations | ✅ | Optional | ❌ | ❌ | — | — | ✅ |
| `format=metadata` for search | ❌ | ❌ | ✅ | ✅ | — | — | ❌ |
| Native batch endpoint | ❌ | ❌ | ❌ | ❌ | — | — | ❌ |
| Batch via Promise.all | ✅ | ❌ | ❌ | ✅ | — | — | ✅ |
| Reply threading headers | ✅ | ✅ | ✅ | ✅ | — | — | ✅ |
| **Docs** ||||||||
| `documents.batchUpdate` | — | — | — | — | — | ✅ | — |
| Multi-tab support | — | — | ✅ | — | — | ✅ | — |
| Comments (via Drive API) | — | — | — | — | — | ✅ | — |
| **Calendar** ||||||||
| Free/busy queries | — | ❌ | ✅ | — | — | — | ❌ |
| Recurring event handling | — | ❌ | ✅ | — | — | — | ❌ |
| Timezone handling | — | ✅ | ✅ | — | — | — | ✅ |

Legend: ✅ = Yes, ❌ = No, — = Not applicable (service not covered)

---

## Pattern Catalog

### 1. Native Markdown Export (felores)

**File:** `/tmp/mcp-analysis/gdrive-mcp-server/index.ts:68-91`

```typescript
case "application/vnd.google-apps.document":
  exportMimeType = "text/markdown";  // Google exports as markdown!
  break;
// ...
const res = await drive.files.export(
  { fileId, mimeType: exportMimeType },
  { responseType: "text" },
);
```

**Why it matters:** Single API call, native conversion by Google, preserves links and basic formatting. Most implementations export as `text/plain` (taylorwilsdon) or don't export at all (gemini-cli redirects to Docs API).

---

### 2. Working Folder Pattern (aaronsb)

**File:** `/tmp/mcp-analysis/google-workspace-mcp/src/utils/workspace.ts`

```typescript
export class WorkspaceManager {
  private basePath: string;

  constructor() {
    this.basePath = process.env.WORKSPACE_BASE_PATH || '/app/workspace';
  }

  private getAccountPath(email: string): string {
    return path.join(this.basePath, email);
  }

  private getDownloadsPath(email: string): string {
    return path.join(this.getAccountPath(email), 'downloads');
  }
}
```

**Structure:**
```
$WORKSPACE_BASE_PATH/
  ├── user@example.com/
  │   ├── downloads/
  │   └── uploads/
  └── shared/
      └── temp/
```

**Why it matters:** Content goes to disk, tools return paths. Caller uses file tools to slice. Token-efficient, persistent, greppable.

---

### 3. Tool Tier System (taylorwilsdon)

**File:** `/tmp/mcp-analysis/google_workspace_mcp/core/tool_tiers.yaml`

```yaml
gmail:
  core:
    - search_gmail_messages
    - get_gmail_message_content
    - send_gmail_message
  extended:
    - get_gmail_attachment_content
    - get_gmail_thread_content
    - modify_gmail_message_labels
  complete:
    - batch_modify_gmail_message_labels
    - start_google_auth
```

**CLI usage:** `--tool-tier core` exposes only essential tools.

**Why it matters:** Controls cognitive load. LLMs work better with fewer, well-chosen tools. Progressive disclosure.

---

### 4. Gmail Filter Templates (GongRzhe)

**File:** `/tmp/mcp-analysis/Gmail-MCP-Server/src/filter-manager.ts:128-186`

```typescript
const templates = {
  fromSender: (senderEmail: string, labelIds: string[] = [], archive = false) => ({
    criteria: { from: senderEmail },
    action: { addLabelIds: labelIds, removeLabelIds: archive ? ['INBOX'] : undefined }
  }),

  mailingList: (listIdentifier: string, labelIds: string[] = [], archive = true) => ({
    criteria: { query: `list:${listIdentifier} OR subject:[${listIdentifier}]` },
    action: { addLabelIds: labelIds, removeLabelIds: archive ? ['INBOX'] : undefined }
  }),

  largeEmails: (sizeInBytes: number, labelIds: string[] = []) => ({
    criteria: { size: sizeInBytes, sizeComparison: 'larger' },
    action: { addLabelIds: labelIds }
  })
};
```

**Why it matters:** Encodes best practices. User says "filter mailing list X" instead of constructing filter JSON.

---

### 5. Multi-Account by Design (mcp-gsuite)

**File:** `/tmp/mcp-analysis/mcp-gsuite/src/mcp_gsuite/gauth.py`

```python
class AccountInfo(pydantic.BaseModel):
    email: str
    account_type: str  # e.g., "work", "personal"
    extra_info: str    # Context for AI: "Use for ITV communications"
```

**Every tool signature:**
```python
def query_gmail_emails(__user_id__: str, query: str, ...):
    setup_oauth2(user_id=__user_id__)  # Load that account's credentials
```

**Why it matters:** Multi-account isn't bolted on - it's the foundation. The `extra_info` field lets Claude know which account to use for what purpose.

---

### 6. Session Security Binding (taylorwilsdon)

**File:** `/tmp/mcp-analysis/google_workspace_mcp/auth/oauth21_session_store.py`

```python
class OAuth21SessionStore:
    def __init__(self):
        self._session_auth_binding: Dict[str, str] = {}  # Immutable once set

    def bind_session_to_user(self, session_id: str, user_email: str):
        if session_id in self._session_auth_binding:
            existing = self._session_auth_binding[session_id]
            if existing != user_email:
                raise SecurityViolation("Cannot rebind session to different user")
        self._session_auth_binding[session_id] = user_email
```

**Why it matters:** Prevents session hijacking across accounts. Once a session authenticates as user A, it cannot access user B's data.

---

## Anti-Pattern Catalog

### 1. Fake Batch Operations (GongRzhe)

**File:** `/tmp/mcp-analysis/Gmail-MCP-Server/src/index.ts:804`

**The claim:** "Batch operations with configurable batch size (default: 50)"

**The reality:**
```typescript
const results = await Promise.all(
  batch.map(async (messageId) => {
    const result = await gmail.users.messages.modify({...});
    return { messageId, success: true };
  })
);
```

This is just parallel individual requests, not Gmail's `users.messages.batchModify` endpoint. Problems:
- Each request counts against rate limits individually
- No atomicity - partial failures leave inconsistent state
- SSL connection exhaustion at scale (taylorwilsdon limits to 25 for this reason)

**Better:** Use Gmail's actual batch endpoint, or be honest about parallel requests.

---

### 2. Export to text/plain (taylorwilsdon)

**File:** `/tmp/mcp-analysis/google_workspace_mcp/gdrive/drive_tools.py:154-158`

```python
export_mime_type = {
    "application/vnd.google-apps.document": "text/plain",  # WHY?
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}.get(mime_type)
```

Google Docs support `text/markdown` export natively. Exporting as `text/plain`:
- Loses all headings, bold, italic
- Loses all links
- Loses lists structure
- Produces wall of text

**Better:** Use `text/markdown` for Google Docs.

---

### 3. Cloud Function for Auth (gemini-cli)

**Architecture:**
```
Client → Google OAuth → Cloud Function (has secret) → Local callback
```

The client secret never lives locally - a cloud function at `google-workspace-extension.geminicli.com` holds it.

**Problems:**
- External dependency for every token refresh
- Latency added to auth flow
- Privacy: Google sees auth requests go through their function
- If function goes down, MCP breaks

**Better:** Standalone OAuth with local client secret (what your fork does with `itv-auth` branch).

---

### 4. Tool Redirection Instead of Export (gemini-cli)

**File:** `/tmp/mcp-analysis/workspace/workspace-server/src/tools/drive/downloadFile.ts`

```typescript
const googleWorkspaceFileMap = {
  'application/vnd.google-apps.document': { tool: 'docs.getText', ... },
  'application/vnd.google-apps.spreadsheet': { tool: 'sheets.getText', ... },
};
// Returns: "Please use the 'docs.getText' tool with documentId: {fileId}"
```

When you ask to download a Google Doc, it tells you to use a different tool. This:
- Doubles the round-trips (discover file type → call different tool)
- Breaks workflow (user wanted one thing, got instructions instead)
- Loses context (file ID needs to be re-supplied)

**Better:** Handle Google Workspace files inline via export.

---

## Content Delivery Comparison

| Implementation | Drive Content | Gmail Content | Attachments |
|----------------|---------------|---------------|-------------|
| taylorwilsdon | Inline string | Inline string | Separate fetch |
| aaronsb | **File deposit** → path | Inline | **File deposit** → path |
| gemini-cli | Redirect to tools | Inline JSON | Download to local path |
| GongRzhe | — | Inline | Download to local path |
| felores | Inline (small) | — | Base64 inline |
| a-bonus | Inline (text/json/markdown) | — | — |
| mcp-gsuite | — | Inline | Save to path or inline |

**Only aaronsb implements true file deposit pattern** - content goes to `$WORKSPACE_BASE_PATH/{email}/downloads/`, tool returns `{ filePath: "/path/to/file.md" }`.

---

## Pagination Comparison

| Implementation | Pattern | Caller Burden |
|----------------|---------|---------------|
| taylorwilsdon | Token in response string, re-call with `page_token` param | High - must parse text |
| aaronsb | Not implemented | N/A |
| gemini-cli | `nextPageToken` in JSON response | Medium - JSON parse |
| GongRzhe | Not implemented | N/A |
| felores | Not implemented | N/A |
| a-bonus | `pageToken` in tool params | Medium |
| mcp-gsuite | Not implemented | N/A |

**No implementation uses opaque cursors that hide pagination mechanics.** All expose raw page tokens.

---

## Authentication Comparison

| Implementation | OAuth Version | Token Storage | Multi-Account | Refresh Handling |
|----------------|---------------|---------------|---------------|------------------|
| taylorwilsdon | 2.0 or 2.1 | File per user | ✅ Session-bound | Auto with retry |
| aaronsb | 2.0 | File + env var | ✅ Per-email clients | Auto via wrapper |
| gemini-cli | 2.0 | Keychain → File fallback | ❌ Single | Via cloud function |
| GongRzhe | 2.0 | `gcp-oauth.keys.json` | ❌ Single | Manual |
| felores | 2.0 | `~/.gdrive-server/credentials.json` | ❌ Single | Auto |
| a-bonus | 2.0 | File | ❌ Single | Auto |
| mcp-gsuite | 2.0 | `.oauth.{email}.json` per account | ✅ By design | Auto |

---

## Recommendations

### 1. Framework Choice

**Recommendation: Raw MCP SDK (TypeScript)**

Rationale:
- FastMCP adds abstraction without clear benefit for this use case
- Raw SDK is well-documented, stable, typed
- All implementations use stdio - no need for framework's transport abstraction
- felores shows a 200-line implementation covers Drive - complexity is optional

Alternative: Python MCP SDK if you want Python ecosystem (uv, better Google client libraries).

### 2. Tool Vocabulary

Building on your find/fetch/file pattern:

```
DISCOVERY
├── find_emails(query, account?) → [{id, thread_id, subject, from, date, snippet}]
├── find_files(query, account?) → [{id, name, mime_type, modified, path}]
├── find_events(time_range, account?) → [{id, summary, start, end, attendees}]
└── find_contacts(query, account?) → [{email, name, org}]

FETCH (deposits to working folder, returns paths)
├── fetch_email(id, account?) → {path, thread_path?}
├── fetch_file(id, account?) → {path, mime_type}
├── fetch_thread(thread_id, account?) → {path}
└── fetch_attachment(message_id, attachment_id, account?) → {path}

MUTATE (minimal surface)
├── send_email(to, subject, body, reply_to?, account?)
├── create_draft(to, subject, body, account?)
├── create_event(summary, start, end, attendees?, account?)
└── move_file(file_id, folder_id, account?)
```

Key principles:
- **Discovery returns metadata only** - IDs, snippets, not full content
- **Fetch deposits to disk** - Working folder per account
- **Mutate is minimal** - Only common write operations
- **Account is optional** - Default to primary, explicit for multi-account

### 3. API Usage Recommendations

| Area | Recommendation | Source |
|------|----------------|--------|
| Drive Docs | Export as `text/markdown` | felores |
| Drive Sheets | Export as `text/csv` | All agree |
| Drive Slides | Export as `text/plain` (no markdown support) | All agree |
| Gmail search | Use `format=metadata` | GongRzhe |
| Gmail read | Use `format=full` | All agree |
| Gmail batch | Use actual batch endpoint or be honest | — |
| All APIs | Add `fields` parameter for partial responses | Nobody does this |
| Drive | Support `supportsAllDrives` for Shared Drives | felores, aaronsb |

### 4. File Deposit Architecture

```
$WORKSPACE_BASE_PATH/
├── config/
│   ├── accounts.json          # Multi-account metadata
│   └── credentials/
│       └── {email}.json       # Per-account OAuth tokens
├── {email}/
│   ├── mail/
│   │   ├── {message_id}.md    # Fetched emails
│   │   └── threads/
│   │       └── {thread_id}.md # Fetched threads
│   ├── drive/
│   │   └── {file_id}.{ext}    # Fetched files
│   └── attachments/
│       └── {message_id}/
│           └── {filename}     # Downloaded attachments
└── shared/
    └── temp/                  # Ephemeral working files
```

**Key design decisions:**
1. **Account isolation** - Each email gets its own directory
2. **ID-based filenames** - `{message_id}.md` not `{subject}.md` (avoids collisions)
3. **Markdown for emails** - Convert HTML body to markdown on fetch
4. **Preserve original extension** - Drive files keep their exported extension
5. **Attachment organization** - Grouped by source message

### 5. What Your Prototype Should Be Evaluated Against

Based on this analysis, evaluate your prototype on:

| Criterion | Questions |
|-----------|-----------|
| **Markdown export** | Does it use `text/markdown` for Google Docs? |
| **File deposit** | Does it write to disk and return paths? |
| **Multi-account** | Is account a first-class parameter? |
| **Discovery vs fetch** | Are metadata and content separate operations? |
| **Token efficiency** | Does find return snippets, not full content? |
| **Partial responses** | Does it use `fields` parameter? |
| **Error messages** | Are API errors transformed to actionable guidance? |
| **Pagination** | Is it opaque or does caller manage tokens? |

---

## Appendix: Repository URLs

| Repository | URL |
|------------|-----|
| taylorwilsdon/google_workspace_mcp | https://github.com/taylorwilsdon/google_workspace_mcp |
| aaronsb/google-workspace-mcp | https://github.com/aaronsb/google-workspace-mcp |
| gemini-cli-extensions/workspace | https://github.com/gemini-cli-extensions/workspace |
| GongRzhe/Gmail-MCP-Server | https://github.com/GongRzhe/Gmail-MCP-Server |
| a-bonus/google-docs-mcp | https://github.com/a-bonus/google-docs-mcp |
| felores/gdrive-mcp-server | https://github.com/felores/gdrive-mcp-server |
| MarkusPfundstein/mcp-gsuite | https://github.com/MarkusPfundstein/mcp-gsuite |
