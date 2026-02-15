# mise-en-space

## Status

**Robustness:** Beta — actively developed
**Works with:** Claude Code, Amp, Gemini CLI (any MCP client)
**Install:** Configure as MCP server (see below)
**Requires:** Python 3.11+, Google OAuth credentials

An MCP sous-chef for Google Workspace that provides a *mise en place* for knowledge work. Peel and pith removed, everything prepped and in its place, ready for Claude to cook with.

## Why another tool for LLMs to use Google Workspace?

[Google's official Workspace MCP](https://github.com/gemini-cli-extensions/workspace) ![Stars](https://img.shields.io/github/stars/gemini-cli-extensions/workspace?style=social) has 50 tools and requires multiple round-trips for basic tasks — search Gmail, get back a list of IDs, call again for each message, all of it burning context. Because it's essentially a thin wrapper over the Workspace APIs, the tool definitions alone take up ~15k of tokens every session.

Looking around for others, I found plenty of inspiration, but also some snags:
    
 - [taylorwilsdon/google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp) ![Stars](https://img.shields.io/github/stars/taylorwilsdon/google_workspace_mcp?style=social) covers every Google service, but returns all content inline — a 70-slide deck or 30-message thread lands straight in your context window
 - [felores/gdrive-mcp-server](https://github.com/felores/gdrive-mcp-server) ![Stars](https://img.shields.io/github/stars/felores/gdrive-mcp-server?style=social) deposits files to disk (Docs→Markdown, Sheets→CSV) the way I wanted, and also used a clever trick to get Drive to do high quality conversions, but only does Google Drive, so its coverage was limited for my needs
 - [GongRzhe/Gmail-MCP-Server](https://github.com/GongRzhe/Gmail-MCP-Server) ![Stars](https://img.shields.io/github/stars/GongRzhe/Gmail-MCP-Server?style=social) — pre-built Gmail filter templates. Good ergonomics for a single source, but again, just a single source. 
- [aaronsb/google-workspace-mcp](https://github.com/aaronsb/google-workspace-mcp) ![Stars](https://img.shields.io/github/stars/aaronsb/google-workspace-mcp?style=social) — deposits files to disk with per-account folders. The right idea for file handling IMO - don't spam the caller's context window, but I didn't need multi-account support
- [a-bonus/google-docs-mcp](https://github.com/a-bonus/google-docs-mcp) ![Stars](https://img.shields.io/github/stars/a-bonus/google-docs-mcp?style=social) — tab-aware Docs extraction. Everyone else ignores multi-tab documents.

I wanted something that had the best of all these ideas:

- **Sous-chef philosophy.** Fetch a doc and get the comments too. Fetch an email and get the attachments extracted. Don't make the chef ask for every ingredient separately.
- **Clean extraction.** Web pages arrive as clean markdown without nav bars. PDFs use hybrid extraction ([markitdown](https://github.com/microsoft/markitdown) → Drive OCR fallback). Office files convert automatically.
- **Opinionated, LLM-first control surface** 3 tools not 50 - search, fetch, create. ~3k tokens of tool definitions and everything routes through the same three verbs.
- **One call, rich results.** Gmail search returns subjects, senders, snippets, and attachment names — not a bag of IDs requiring N+1 follow-ups.
- **Filesystem-deposits.** Content goes to disk as markdown/CSV, not into the context window. Claude reads (and greps) what it needs.
- **Companion Skill.** I like the pattern where we provide a tool and a companion [Skill](https://docs.anthropic.com/en/docs/claude-code/skills) that acts as the instruction manual on how to use it.
- **[MCP](https://modelcontextprotocol.io) Optional.** Option for CLI based interactions e.g. if you want to use a different agent harness like pi.

## The 3 Verbs

| Verb | Purpose | Deposits files? |
|------|---------|-----------------|
| `search` | Find files and emails across Drive and Gmail | Yes — results JSON |
| `fetch` | Extract content to `mise-fetch/` as markdown/CSV | Yes — content folder |
| `create` | Make a new Doc/Sheet/Slides from markdown | No |

## CLI

Same 3 verbs, for agents without MCP support:

```bash
mise search "quarterly reports"
mise search "from:alice budget" --sources gmail
mise fetch 1abc123def456
mise fetch "https://simonwillison.net/..."
mise create "Title" --content "# Markdown content"
```

## Supported Content Types

| What's in the larder | What the chef gets |
|--------|-------------|
| Google Docs | Markdown + open comments |
| Google Sheets | CSV + chart PNGs + open comments |
| Google Slides | Markdown + selective thumbnails + open comments |
| Gmail threads | Markdown with signature stripping via [talon](https://github.com/mailgun/talon), attachment extraction |
| PDFs | Markdown ([markitdown](https://github.com/microsoft/markitdown) → Drive OCR fallback) |
| Office files (DOCX/XLSX/PPTX) | Markdown or CSV via Drive conversion |
| Web URLs | Clean article extraction as markdown using [trafilatura](https://github.com/adbar/trafilatura), JS rendering fallback |
| Video/Audio | AI summary + metadata (requires claude-suite) |
| Images | Deposited as-is; SVG rendered to PNG |

## Architecture

```
server.py       MCP server (thin wrappers around tools)
cli.py          CLI interface (same verbs, same tools)
tools/          Business logic — routing, orchestration
adapters/       Thin Google API wrappers (one per service)
extractors/     Pure functions, no I/O (testable without APIs)
workspace/      File deposit management
skill/          Claude skill (symlinked to ~/.claude/skills/mise)
```

**Layer rules:**
- Extractors never import from adapters (no I/O)
- Tools wire adapters → extractors → workspace
- Server and CLI are both thin wrappers around tools

Adding a new content type means: adapter (API call), extractor (parse), tool (wire + deposit). The layers are independent.

## Setup

### 1. Clone and install

```bash
git clone https://github.com/spm1001/mise-en-space.git
cd mise-en-space
uv sync    # requires uv — https://docs.astral.sh/uv/
```

### 2. Google OAuth

mise-en-space uses `itv-google-auth` (private package) for OAuth. You need a Google Cloud project with the Drive, Gmail, and Slides APIs enabled.

```bash
# First run will trigger OAuth flow
uv run python cli.py search "test"
```

### 3. Add to Claude as MCP server

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "mise": {
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "/path/to/mise-en-space", "run", "python", "server.py"]
    }
  }
}
```

### 4. Link the skill (recommended)

The `skill/` directory contains a Claude skill that teaches Claude *how* to use mise effectively — Gmail operators, the exploration loop, comment checking patterns.

```bash
# For Claude Code
ln -s /path/to/mise-en-space/skill ~/.claude/skills/mise

# For pi
ln -s /path/to/mise-en-space/skill ~/.pi/agent/skills/mise
```

Without the skill, Claude can call the tools but won't know the patterns that make them useful (like following `email_context` hints or filtering large results with jq).

## What to Expect (Latency)

MCP server startup is ~1.3s (import + first auth). After that, the server stays alive — subsequent calls skip startup.

| Operation | Typical | Range | Notes |
|-----------|---------|-------|-------|
| **Search (single source)** | ~1s | 0.2–1.3s | Drive and Gmail similar |
| **Search (Drive + Gmail)** | ~0.8s | 0.6–1.1s | Parallel — faster than either alone |
| **Fetch: Web page** | ~0.1s | 0.0–0.2s | HTTP direct, fastest path |
| **Fetch: Google Doc** | ~2s | 1.7–3.1s | Single API call |
| **Fetch: Gmail thread** | ~2.4s | 1.8–3.0s | Thread + message batch |
| **Fetch: PDF** | ~2.5s | 2.1–3.0s | markitdown; complex PDFs fall back to Drive OCR (5–15s) |
| **Fetch: Google Sheet** | ~4s | 1.9–5.9s | 2 API calls (metadata + values) |
| **Fetch: Slides (7 slides)** | ~6s | 3.1–9.3s | ~0.5s per thumbnail, sequential |
| **Fetch: XLSX** | ~6s | 6.1–6.7s | Drive upload → convert → export |
| **Fetch: DOCX** | ~9s | 8.3–9.9s | Same pipeline, larger payloads |

*Benchmarked 9 Feb 2026 at [`fd5f9d0`](../../commit/fd5f9d0), 3 runs each, warm server, London → Google APIs.*

**The slow paths:** Office files (DOCX/XLSX) are unavoidably slow — Drive does server-side conversion (upload → convert → export → cleanup). Gmail attachments that are Office files are listed but not auto-extracted for this reason; use `fetch(thread_id, attachment="file.xlsx")` on demand.

Detailed timing data and flow diagrams: [`docs/information-flow.md`](docs/information-flow.md)

## The Kitchen

Mise en Space is part of [Batterie de Savoir](https://spm1001.github.io/batterie-de-savoir/) — a suite of tools for AI-assisted knowledge work. See the [full brigade and design principles](https://spm1001.github.io/batterie-de-savoir/) for how the tools fit together.

