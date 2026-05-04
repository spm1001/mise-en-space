# Quick-file: Cowork polish from 2026-05-04 field test

Six bons to file when the Dolt backend is reachable. Parent for items 1–2: `mise-jezugi`
(unless reorganised). Items 3–6 are standalone actions / parent under jezugi at filer's
discretion. Created from in-session capture; bon CLI was pointed at a Dolt backend that
wasn't responding when we tried to file directly.

---

## Bon 1 — Mise responses self-disclose authenticated identity in cues

**Parent:** mise-jezugi
**Type:** outcome (or action — depends on whether you want it scoped under
mise-jezugi as a sub-action)

**Why:** When Cowork has multiple Workspace connectors (e.g. native Calendar
bound to one Google account, mise bound to another), Claude has no way to tell
which response came from which identity. Picked the wrong one in field test
2026-05-04 — Cowork's native Calendar (personal account) instead of mise's
calendar (ITV). InnerClaude said it was "flying blind".

**How:** Add `_identity` field to the `cues` block of every search/fetch/do
response. Resolve identity once at token-save time (call `userinfo.get` after
OAuth) and stash the email in the token JSON. Read it cheaply on every call.
Self-disclosing data is more robust than skill or hook copy that may not be
read.

**What:**
1. After successful OAuth in setup_oauth flow, call `userinfo.get` and store
   email in token.json.
2. In `adapters/http_client.py`, expose `current_user_email()` that reads
   from token.
3. In every mise tool response builder, include `cues._identity =
   current_user_email()`.
4. Tests verify the field appears.

**Done:** Every mise response in a Cowork session with multiple Workspace
connectors carries `cues._identity` showing the authenticated email.
Verified by running search and seeing the identity field in the deposit JSON.

---

## Bon 2 — workspace SKILL.md is Cowork-aware

**Parent:** mise-jezugi
**Type:** action

**Why:** Current SKILL.md body has CC-specific instructions ("After
installing this plugin: exit and relaunch Claude Code (`/exit` then `claude`)")
that don't apply in Cowork — confusing for non-CC users. Visible in Cowork's
Customize UI as part of the skill description (screenshot 2026-05-04 17:10:06).

**How:** Replace CC-specific reactivation language with runtime-agnostic
guidance. Add a paragraph naming the multi-account hygiene problem: mise is
bound to whichever Google account authenticated it; other Workspace connectors
in the same Cowork session may be bound to different accounts. When in doubt,
prefer mise's `mcp__plugin_mise_mise__*` tools to surface ITV-side data
explicitly.

**What:**
1. Remove the "After Installing This Plugin" section's `/exit` then `claude`
   reactivation text.
2. Add an "Identity & multi-account" section.
3. Add a one-liner pointing at `mise.do(operation="setup_oauth")` for first-run.

**Done:** Workspace skill description renders cleanly in Cowork's Customize UI
without CC-specific reactivation chatter, with multi-account hygiene noted.

---

## Bon 3 — Field report: connector disambiguation in Cowork

**Type:** standalone action

**Why:** First field test of mise as a Cowork plugin (2026-05-04) surfaced a
real ambiguity that affects all multi-connector Cowork users. Worth a short
write-up so the Anthropic-facing wishlist has a concrete repro.

**What:** Capture: connectors are UUID-named in tool list with no account
binding visible to Claude. Three layers (declared/verified/structural) per
InnerClaude's analysis. What plugin authors can do (`cues._identity`,
SKILL.md hygiene). What only Anthropic can do (renamable connectors in UI,
identity badges next to each instance).

**Done:** `docs/2026-05-04-cowork-connector-disambiguation.md` exists;
captures repro, three-layer analysis, what's plugin-author-fixable vs
Anthropic-only.

---

## Bon 4 — Unit tests for tools/setup_oauth.py

**Parent:** mise-jezugi (or standalone)
**Type:** action

**Why:** Coverage report after the 2026-05-04 implementation showed setup_oauth.py
at 33% — basically just the imports. The branches that matter (has_token short-circuit,
missing credentials.json, port 3000 busy, subprocess spawn failure) all go untested.
Future regressions in any of those will land silently and only surface as Friday-style
field issues.

**How:** Mock `socket.socket().bind` to simulate port-busy. Mock `has_token` and
`LOCAL_CREDENTIALS_FILE.exists` for the early-return paths. Mock `subprocess.Popen` so
we don't actually spawn anything. Assert the response shape (status, url, cues fields)
for each branch.

**What:**
1. tests/unit/test_setup_oauth.py covering:
   - Already-authenticated short-circuit path
   - Missing credentials.json path
   - Port 3000 busy path
   - Successful spawn returns DoResult-shaped dict
2. Each test asserts both error/success kind and the user-facing message text.

**Done:** setup_oauth.py coverage above 80%; CI green.

---

## Bon 5 — auth.py --auto pre-checks port 3000 like the MCP tool does

**Parent:** mise-jezugi (or standalone)
**Type:** action

**Why:** Parity gap. tools/setup_oauth.py runs `_port_is_free(OAUTH_PORT)` before
spawning the auth subprocess; auth.py --auto goes straight to jeton.authenticate()
which fails ungracefully if the port is held by something else (Node dev server is
the classic). The CLI fallback should be at least as user-friendly as the MCP tool.

**How:** Lift `_port_is_free` from tools/setup_oauth.py into a shared helper
(e.g. `oauth_config.py` or a new `port_check.py`). Call from both auth.py --auto
and tools/setup_oauth.py.

**What:**
1. Move `_port_is_free` to a shared location.
2. auth.py --auto: pre-check port; on failure, print actionable message and exit 1
   (don't proceed into jeton.authenticate which will OSError).
3. tools/setup_oauth.py: import the shared helper.
4. One unit test for the helper.

**Done:** Running `uv run python -m auth --auto` with port 3000 held by another
process exits cleanly with a clear error, not a stack trace.

---

## Bon 6 — Investigate Cowork plugin platform compatibility hints

**Parent:** mise-jezugi (or standalone)
**Type:** action

**Why:** MCPB's manifest.json supports `compatibility.platforms: ["darwin", "linux"]`
to gate installs. Cowork's `.claude-plugin/plugin.json` doesn't appear to expose the
same hook. Mise won't run on Windows (hardcoded uv + python3.11 + macOS Keychain
expectations), but nothing in the bundle prevents a Windows install — failure mode
would be confusing ("uv: command not found" or stdio server immediately exits).

**How:** Inspect Anthropic's plugin schema docs (code.claude.com/docs/en/plugins
references it). If a compatibility/requirements field exists, add it. If not, file
upstream and add a runtime guard in server.py (`if sys.platform not in ("darwin",
"linux"): print friendly error; exit`).

**What:**
1. Read current plugin schema docs.
2. If schema supports it: add platform field.
3. If not: add a `sys.platform` guard at server.py top with a friendly error message
   pointing at the limitation.
4. Note in CLAUDE.md what the constraint is.

**Done:** Either plugin.json declares the platform constraint OR server.py exits
cleanly on Windows with a clear message and the constraint is documented.
