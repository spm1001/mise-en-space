# uv-tool route for the mise engine: overnight probe, 27–28 Sep 2026

Question (bds-sovabu on the batterie-de-savoir board, option (e)): can the mise engine be installed like bon — a uv tool installed from a wheel batterie ships — with each ring kit (mit@mit, family@family) carrying only a server line and its own OAuth client? Run on tube by the batterie-de-savoir session on Sameer's ask, in scratch dirs and on this branch only (no push to main; the 28 Sep freeze held).

## What changed on this branch

- `mise_en_space/serve.py` (new): a console entry point, `mise-en-space-server`, that runs `server.py` as `__main__` via `runpy`. The first version moved server.py's `__main__` body into a `main()`, which broke `test_server_stays_thin` (506 > 500 lines); server.py is now untouched, the suite passes, and T4 and M-A 1 were re-run on the rebuilt wheel (same results).
- `pyproject.toml`: the wheel now carries `server.py`, `org_map.json` and the `resources` package, and no `credentials.json` (so the wheel binds no Workspace); `jeton` gained a floor (`jeton>=1.4`); the CLI script was renamed `mise` → `mise-en-space` (see T3).

Wheel: 471 KB, `mise_en_space-1.87.0-py3-none-any.whl`.

## Results

| # | Test | Result |
|---|---|---|
| T1 | Engine wheel installed with no jeton source named | Refuses: "only jeton==0.1.0 is available and mise-en-space depends on jeton>=1.4". Without the floor, bare `jeton` resolves to PyPI's jeton 0.1.0 (a stranger's package; mise-fafono). |
| T2 | `uv tool install 'mise-en-space[extraction] @ file://…whl' --with <jeton wheel>` | Installs; jeton 1.4.1 comes from the wheel (direct_url.json names it). |
| T3 | Same install with a stand-in jdx/mise already at `bin/mise` | Without `--force`: refuses. With `--force` (as batterie's hooks run installs): **clobbers it**. So the engine must not ship a `mise` console script. |
| T4 | Installed server, ITV client via `MISE_EN_SPACE_OAUTH_CLIENT`, fresh data dir | Answers as sameer.modha@itv.com. The pre-seam token (`~/.claude/plugins/data/mise-batterie-de-savoir/token.json`) is adopted by copy: same sha256, 0600; the original's hash and mtime are unchanged. |
| T5 | No client supplied (and none bundled) | Search returns `errors: ["… No OAuth token found … call setup_oauth"]`. Loud, but the wrong advice: the real gap is no client. Engine wording is bds-sovabu step 2. |
| T6 | Same installed engine, family's planetmodha client | Answers as sameer@planetmodha.com. One install, two Workspaces, by env alone. |
| T7a | Claude Code, fresh machine, ring kit's server line = bare `mise-en-space-server`, engine installed by another plugin's SessionStart hook | **Fails.** MCP spawn at +1.67 s, hook finished install at +2.0 s: servers start in parallel with SessionStart hooks, not after them. ENOENT. |
| T7b | Next session, engine now installed | **Server not even attempted.** Claude Code had written `plugin:ringprobe:mise` into `~/.claude/mcp-needs-auth-cache.json` at the ENOENT. |
| T7c | Cache entry removed, engine installed | Connects in 5.0 s, answers as sameer.modha@itv.com. |
| M-A 1 | Ring kit's server line = `${CLAUDE_PLUGIN_ROOT}/launch/mise.sh`; fresh tool dir, **cold uv cache**, tool bin dir **not on PATH** | Works first time. Launcher started before the hook, found nothing on PATH, found the engine via `uv tool dir --bin` after 6 × 0.5 s polls, exec'd it; connected in 7.7 s; ITV identity; no needs-auth entry. |
| M-A 2 | Control: no plugin installs the engine | Launcher gives up at 20 s with a plain stderr line ("the Google Workspace engine is not installed. batterie@batterie installs it at session start — restart Claude Code…"); CC logs it and caches needs-auth. The check can fail. |
| M-A 3 | Engine present; two ring kits (ITV, planetmodha) in one session | Both connect (2.8 s, 3.6 s), each with its own identity and its own token store, one engine install. |
| U1 | `uv tool install --force --reinstall` of a bumped engine under a running server | Server survives and keeps serving. Of the engine's own modules only `adapters.ambient` is not imported at start, and it imported cleanly after the swap. Third-party lazy imports untested. |

Cold install (empty uv cache, PyPI over tube's link): 2.0 s with the `extraction` extra, 1.1 s core only; the tool env is 248 MB (74 MB core), once per machine. Today each plugin version builds its own ~333 MB `.venv`.

## The Claude Code behaviour that decides the design

A plugin stdio MCP server that fails to spawn is recorded in `~/.claude/mcp-needs-auth-cache.json` as `{timestamp, id}` and skipped by later sessions until the entry expires. In the 2.1.283 binary the TTL is `u.ttlMs ?? (h ? 14400000 : 900000)`, where `h` is true for claude.ai-proxy servers and plugin http/sse servers: so **15 minutes for a plugin stdio server**, 4 hours for plugin http/sse. A first-session failure therefore costs 15 minutes of sessions, not one. This applies to today's batterie mise too if its `uv run` spawn ever fails (e.g. a cold sync beyond the 30 s connect timeout).

Hence the launcher: the ring kit's server command must exist from the first instant (no ENOENT), and must wait for batterie's hook rather than fail.

## Mac facts (read-only over ssh)

uv is at `/opt/homebrew/bin/uv`, but `uv tool dir --bin` is `~/.local/bin` (where bon, accomplis and deglacer live). A GUI surface whose PATH has Homebrew but not `~/.local/bin` would find uv yet miss a bare engine command — the case the launcher's `uv tool dir --bin` fallback exists for. Not run on the Desktop app or Cowork.

## Not tested

Desktop app and Cowork; a slow network against the 20 s wait and the 30 s connect timeout; the Codex surface; the atelier's first login; third-party lazy imports across an upgrade.

## Addendum, 28 Sep ~01:00: the review refuted the uv-tool route; the design moved to a pointer

A cold essayeur review of the uv-tool install (step "2b", `uv tool install --force --reinstall` of the engine from the kit's wheel) found, with reproductions:

- **Concurrent reinstalls under a running engine break it and then read as current.** Two hooks racing while a process imported from the tool env: 4 of 12 installs failed (`failed to remove directory …: Directory not empty`), and 3 of 8 envs were left broken. One had no `bin/` and 81 of 181 packages, yet `direct_url.json` named the new wheel, so later runs skipped the repair.
- **Unlocked env, different Python.** 12 of 75 packages differed from `uv.lock` (onnxruntime 1.30.0 vs 1.20.1), on CPython 3.14 instead of the pinned 3.11, and the `tools/list` schema hash differed. Ring kits would run an engine nobody tests.
- **Unbounded install time at session start** (45 s against a hanging proxy), retried every session. The warm-cache premise was also false: about 47 MB of extra downloads.
- **Version ping-pong.** An older session's hook downgrades the engine, with a 2.1 s window where imports fail.
- **A broken `python3` meant a silent full reinstall every session.**

So the kit no longer installs a second engine. Its start hook completes its own locked `.venv` (with the `extraction` extra) and records `version=` and `root=` in `${XDG_DATA_HOME:-~/.local/share}/mise-en-space/engine`, written by rename and never moved backwards while the newer target is intact. A ring kit's launcher reads the pointer and execs `<root>/.venv/bin/python <root>/server.py`. That is the same env and Python batterie's own server runs and CI tests. Nothing is replaced in place (each plugin version has its own dir), and there is no extra download. In the proofs: six simultaneous hooks for ten rounds never showed a reader an unreadable or dead pointer. And in Claude Code, a fresh kit copy whose hook had to sync the venv (3.2 s) had the MIT launcher wait and connect in 8.2 s, answering as sameer.modha@itv.com. Tests: `tests/test_engine_pointer.py` (red on main's hook), and the launcher's harness in ITV/mit-kit `tests/mise-launcher.sh`.

The findings above that still hold: servers start alongside SessionStart hooks, a failed plugin stdio spawn is skipped for 15 minutes, and a launcher that waits is what makes the first session work.
