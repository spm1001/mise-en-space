# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.28.1"]
# ///
"""Fetch a thread-a URL through a cache server's real stdio envelope."""
import asyncio, json, os, sys, tempfile
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_DIR, URL = sys.argv[1], sys.argv[2]

async def main():
    env = {k: v for k, v in os.environ.items() if k in ("HOME", "PATH", "USER", "SHELL", "TERM", "LANG")}
    env["PASSE_CDP"] = "http://localhost:9223"
    params = StdioServerParameters(
        command="uv",
        args=["run", "--project", SERVER_DIR, "--extra", "extraction", "python", f"{SERVER_DIR}/server.py"],
        env=env,
    )
    base = tempfile.mkdtemp(prefix="wamoco-")
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("fetch", {"file_id": URL, "base_path": base})
            payload = json.loads(res.content[0].text)
            out = {
                "error": payload.get("error"),
                "kind": payload.get("kind"),
                "message_head": (payload.get("message") or "")[:200],
                "type": payload.get("type"),
                "warnings": (payload.get("cues") or {}).get("warnings"),
                "has_candidates": bool(payload.get("candidates")),
            }
            print(json.dumps(out, indent=2))

asyncio.run(main())
