"""
Drive the WORKING TREE's MCP server over real stdio — mise-saroca / mise-tuveda.

Why this exists. The plugin's mcpServers block spawns with
`--project ${CLAUDE_PLUGIN_ROOT}`, i.e. ~/.claude/plugins/cache/batterie/mise/<v>/.
The server therefore always runs PUBLISHED code, so working-tree changes are
unreachable from the Claude Code MCP envelope no matter how often the session
restarts. That makes "smoke through the envelope, then publish" circular: the only
envelope available runs the very code you were trying to test before shipping it.

This closes the gap without publishing blind. It spawns server.py from the repo and
speaks real MCP to it, so it exercises what unit tests structurally cannot: the
FastMCP registration, the @mcp.tool wrapper, argument coercion from the JSON schema,
and the shape of what actually crosses the wire. It is NOT a substitute for a
post-publish check of the installed artefact — the assembler and the flavour
transform sit between this and what users get.

Usage (from the repo root):
    uv run --all-extras python scripts/smoke_stdio.py
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parent.parent

# Each case is (label, fetch input, substrings that MUST appear in the response).
# The ids are the real ones from docs/2026-08-01-usage-review.md.
CASES = [
    (
        "draft id (was a bare 404, the abandoned-retry case)",
        "r8287431168042343092",
        ["draft", "404 forever"],
    ),
    (
        "chat link (jujoti refusal, already shipped — regression guard)",
        "https://mail.google.com/chat/u/0/#chat/space/AAQAXm1PxYs",
        ["Google Chat link"],
    ),
    (
        "garbage drive-shaped id",
        "1ZZZnotarealdriveidXXXXXXXXXXXXXXXXXXXXXX",
        ["not that the id is malformed"],
    ),
    (
        "REAL mid-thread message id — the 7-minute detour of 2026-07-31. "
        "This one SUCCEEDS: the fallback resolves it and the rescue is disclosed",
        "18fe27655760c61b",
        ["18fd8caa12fed511", "is a Gmail MESSAGE id, not a thread id"],
    ),
]


async def main() -> int:
    params = StdioServerParameters(
        command="uv",
        args=[
            "run", "--project", str(REPO), "--extra", "extraction",
            "python3", str(REPO / "server.py"),
        ],
    )

    failures = 0
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name for t in (await session.list_tools()).tools}
            print(f"tools advertised: {sorted(tools)}\n")

            for label, file_id, expected in CASES:
                result = await session.call_tool(
                    "fetch", {"file_id": file_id, "base_path": "/tmp/mise-smoke"}
                )
                text = "".join(
                    getattr(block, "text", "") for block in result.content
                )
                missing = [want for want in expected if want not in text]

                status = "PASS" if not missing else "FAIL"
                if missing:
                    failures += 1
                print(f"[{status}] {label}")
                print(f"   input: {file_id}")

                # Print the WHOLE evidence, never a window onto it. Two ways this
                # display misled its author within a minute of being written, both
                # worth not re-learning: it truncated at 400 chars, which cut off the
                # teaching text these cases exist to check (Google's 404 URL alone eats
                # ~350), and it printed only `message`, which is absent on success — so
                # the one case that WORKED rendered as a blank line. A probe's display
                # is part of the probe: a truncated view reads as a null and a null
                # reads as a failure.
                try:
                    payload = json.loads(text)
                except ValueError:
                    print(f"   raw: {text}")
                else:
                    if payload.get("error"):
                        # Strip Google's enormous URL so the teaching half is readable.
                        msg = payload.get("message", "")
                        print(f"   error: {msg.split('| API:')[-1].strip()}")
                    else:
                        print(f"   ok: deposited {payload.get('type')} → {payload.get('path')}")
                        for warning in (payload.get("cues") or {}).get("warnings") or []:
                            print(f"   cue: {warning}")
                if missing:
                    print(f"   MISSING: {missing}")
                print()

    print(f"{len(CASES) - failures}/{len(CASES)} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
