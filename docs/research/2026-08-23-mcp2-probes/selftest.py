# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==2.0.0"]
# ///
"""Known-good control: connect in-memory, once WITHOUT an elicitation callback
(capability should be absent) and once WITH one (capability should appear,
and confirm_test should round-trip)."""
import asyncio, json
from mcp import Client
from probe_server import mcp as server


async def main():
    async with Client(server) as client:
        r = await client.call_tool("client_caps", {})
        print("NO-CALLBACK caps:", json.dumps(r.structured_content, indent=2))

    async def answer(context, params):
        from mcp.types import ElicitResult
        return ElicitResult(action="accept", content={"proceed": True})

    async with Client(server, elicitation_callback=answer) as client:
        r = await client.call_tool("client_caps", {})
        print("WITH-CALLBACK caps:", json.dumps(r.structured_content, indent=2))
        r2 = await client.call_tool("confirm_test", {})
        print("confirm_test:", r2.content[0].text if r2.content else r2.structured_content)


asyncio.run(main())
