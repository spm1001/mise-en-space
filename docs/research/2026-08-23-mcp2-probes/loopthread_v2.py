# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp[cli]==2.0.0"]
# ///
"""Does a sync tool body see a running event loop through the REAL stdio envelope?"""
import asyncio
from mcp.server.mcpserver import MCPServer as FastMCP

mcp = FastMCP("loopprobe")

@mcp.tool()
def probe() -> dict:
    try:
        asyncio.run(asyncio.sleep(0))
        return {"asyncio_run": "OK — no running loop on this thread"}
    except RuntimeError as e:
        return {"asyncio_run": f"RuntimeError: {e}"}

if __name__ == "__main__":
    mcp.run()
