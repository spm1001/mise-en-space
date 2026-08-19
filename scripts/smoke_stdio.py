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
    (
        "REAL Message-ID (from Show original) — resolves via rfc822msgid: "
        "and the resolution is disclosed (mise-lerulo)",
        "<VI0PR01MB11914227C6036AFC7DC888B59E8D12"
        "@VI0PR01MB11914.eurprd01.prod.exchangelabs.com>",
        ["19fdaeed11138ef2", "rfc822msgid"],
    ),
    (
        "REAL Show-original URL — permmsgid=msg-f decimal converts to the hex "
        "message id, whose message heads its thread (mise-lerulo). A direct hit "
        "carries no cue, so the deposit path (12-char id prefix) is the evidence",
        "https://mail.google.com/mail/u/0/"
        "?ik=2bb48b24a5&view=om&permmsgid=msg-f:1872845353272970994",
        ["gmail--your-first-gtd-weekly-review--19fdaeed1113"],
    ),
    (
        "Show-original URL for a SELF-SENT message (msg-a) — refused with the "
        "copy-the-Message-ID-from-the-page teaching text",
        "https://mail.google.com/mail/u/0/"
        "?ik=2bb48b24a5&view=om&permmsgid=msg-a:r-8125895545114462359",
        ["msg-a", "Message-ID"],
    ),
    (
        "DEAD draft URL (mise's own #drafts/<id> shape) — the resolution branch "
        "fires, drafts.get 404s, and the error teaches the lifecycle rather than "
        "implying a bad id (mise-jujoti step 7). A LIVE draft would resolve to "
        "its thread, but a pinned live draft rots the moment it is sent, so the "
        "stable envelope case is the expiry path; the success path is unit-tested "
        "and probed live at ship time",
        "https://mail.google.com/mail/#drafts/r1234567890123456789",
        ["sent or discarded", "expired"],
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

            # search's failure surface (mise-riduka): the all-empty refusal must
            # teach the calendar-window routes through the real envelope — the
            # unit tests enter through do_search/server.search, not the wire.
            result = await session.call_tool("search", {"base_path": "/tmp/mise-smoke"})
            text = "".join(getattr(block, "text", "") for block in result.content)
            wanted = ["time_min", "sources=['calendar']"]
            missing = [w for w in wanted if w not in text]
            status = "PASS" if not missing else "FAIL"
            if missing:
                failures += 1
            print(f"[{status}] search with nothing at all — the gate refusal "
                  "teaches the calendar-window routes (mise-riduka)")
            print(f"   raw: {text}")
            if missing:
                print(f"   MISSING: {missing}")
            print()

            # do(create_event) preview (mise-rijeco): the new calendar params
            # (attendees list, time_min/time_max) must cross the wire and coerce,
            # the gate must fire BEFORE any insert, and the clash check runs
            # against the real diary. Read-only: preview books nothing.
            result = await session.call_tool("do", {
                "operation": "create_event",
                "title": "smoke preview (never booked)",
                "time_min": "2026-09-08T14:00",
                "time_max": "2026-09-08T14:30",
                "attendees": ["smoke-probe@example.com"],
                "base_path": "/tmp/mise-smoke",
            })
            text = "".join(getattr(block, "text", "") for block in result.content)
            wanted = ["preview", "confirm=True", "clashes"]
            missing = [w for w in wanted if w not in text]
            status = "PASS" if not missing else "FAIL"
            if missing:
                failures += 1
            print(f"[{status}] do(create_event) with attendees, no confirm — "
                  "previews with a live clash check, books nothing (mise-rijeco)")
            print(f"   raw: {text[:600]}")
            if missing:
                print(f"   MISSING: {missing}")
            print()

    total = len(CASES) + 2
    print(f"{total - failures}/{total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
