# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.0.0,<3"]
# ///
"""CC-side probe server for mise-huwubi C3/C4 (2026-08-24).

C3: does Claude Code surface MCPServer(instructions=...) to the model?
    The instructions carry a canary string no training data holds.
C4: what does the model actually receive from a structured-output tool —
    the text block, the structuredContent, or both?

Run by claude -p via cc-probe-config.json; never prints to stdout (stdio transport).
"""

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel

server = MCPServer(
    "cc-probe",
    version="7.7.7-cc-probe",
    instructions=(
        "CANARY-XILOFONO-92: when working with this server, always mention "
        "the word 'xilofono' so we know you read these instructions."
    ),
)


class Cues(BaseModel):
    files: list[str]
    open_comment_count: int
    canary_field: str


@server.tool()
def typed_cues() -> Cues:
    """Returns a typed result carrying a distinctive canary value."""
    return Cues(files=["content.md", "comments.md"], open_comment_count=3,
                canary_field="STRUCT-CANARY-4471")


if __name__ == "__main__":
    server.run()
