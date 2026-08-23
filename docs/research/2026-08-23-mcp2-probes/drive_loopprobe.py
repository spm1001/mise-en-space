# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.28.1"]
# ///
import asyncio, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command="uv", args=["run", "--script", sys.argv[1]])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("probe", {})
            print(sys.argv[1], "->", res.content[0].text)

asyncio.run(main())
