# mise-nujina — the OAuth-client seam, proved on a throwaway home (2026-09-27)

Card: mise-pubata under mise-nujina (W4 of the estate rebuild). The claim: **one engine that ships no OAuth client authenticates both Workspaces on one throwaway home, with the clients supplied from outside and the existing tokens adopted without a consent click.**

## The rig

- A claude-vanilla world at `/var/tmp/claude-vanilla-modha/nujina` (outside the real `$HOME`, so no `~/.claude` leaks in).
- `engine/` = the branch's tracked tree with `credentials.json` deleted — the engine ships no client.
- `kits/mit/oauth-client.json` (the ITV client) and `kits/family/oauth-client.json` (the planetmodha client, read from the installed mise-home plugin) — the clients "from outside".
- The two real pre-seam tokens copied (0600) into the throwaway's OLD locations, `~/.claude/plugins/data/mise-batterie-de-savoir/` and `~/.claude/plugins/data/mise-home/`. The kit stores (`kit-mit/`, `kit-family/`, standing in for `${CLAUDE_PLUGIN_DATA}`) start empty.
- Every server spawned through `env -i` with `HOME=<throwaway>` and only `MISE_OAUTH_CLIENT` + `MISE_DATA_DIR`.

The whole world, tokens included, was deleted after the run.

## What ran

1. `proof.py` — speaks MCP to each server directly. Output: `proof-output.txt`, 14/14. It covers the no-wiring control, `setup_oauth` answering `already_authenticated` (no consent), a live Drive search answering as each identity, the adopted token's mode and client, the pre-seam store left byte-identical, the cross-wired refusal, the CLI's consent URL carrying each kit's client (print mode, never clicked), and the SessionStart hook silent-until-configured with its nag control.
2. A real Claude Code on the same home (`claude-vanilla nujina -- -p … --mcp-config mcp.json --strict-mcp-config`), kit stores reset first so it had to adopt again. Its transcript shows `mcp__mise__search` answering as `sameer.modha@itv.com` and `mcp__mise-home__search` as `sameer@planetmodha.com`, one Drive result each, with both kit tokens born during that run. Opus 5.5 on Vertex, 4 turns, $0.19.

## Not covered

- The macOS Keychain half (per-client service name, adoption from the old services) is unit-tested with mocks only; non-interactive ssh to the Mac has a locked keychain.
- Cowork: whether `${CLAUDE_PLUGIN_DATA}` survives a Cowork session is unmeasured, which is why the Keychain stays the macOS store in seam mode.
- Packaging: no kit plugin exists yet; `mcp.json` here plays the kit's `mcpServers` entry. bds-jakemi owns the repackaging.

To re-run: rebuild the world as above (copy tokens by hand — the script never reads real token paths), then `uv run --all-extras python docs/research/2026-09-27-nujina-seam-proof/proof.py` from the repo root.
