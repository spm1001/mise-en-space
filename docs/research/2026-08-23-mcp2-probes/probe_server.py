# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==2.0.0"]
# ///
"""Elicitation capability probe.

client_caps            — what the connected client declared at initialize
confirm_test           — era-neutral Resolve(Elicit) confirm gate (the v2 way)
confirm_backchannel    — direct ctx.elicit(), 2025-era back-channel style
"""
from typing import Annotated

from mcp.server.mcpserver import MCPServer, Context, Elicit, Resolve
from pydantic import BaseModel

mcp = MCPServer("elicit-probe")


@mcp.tool()
async def client_caps(ctx: Context) -> dict:
    """Report the capabilities the connected client declared at initialize."""
    params = ctx.session.client_params
    caps = params.capabilities if params else None
    return {
        "client_info": params.client_info.model_dump() if params and params.client_info else None,
        "protocol_version": params.protocol_version if params else None,
        "capabilities": caps.model_dump() if caps else None,
    }


class ConfirmAnswer(BaseModel):
    proceed: bool


def ask_confirm() -> Elicit[ConfirmAnswer]:
    return Elicit(message="Probe: shall the imaginary event be booked?", schema=ConfirmAnswer)


@mcp.tool()
async def confirm_test(
    answer: Annotated[ConfirmAnswer, Resolve(ask_confirm)],
) -> str:
    """Confirm gate via era-neutral resolver injection — the human answers, not the model."""
    return f"human answered: proceed={answer.proceed}"


@mcp.tool()
async def confirm_backchannel(ctx: Context) -> str:
    """Confirm gate via direct ctx.elicit() — 2025-era back-channel style."""
    result = await ctx.elicit(message="Probe (back-channel): book the imaginary event?", schema=ConfirmAnswer)
    return f"elicit outcome: action={result.action}, data={getattr(result, 'data', None)}"


if __name__ == "__main__":
    mcp.run()
