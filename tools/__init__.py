"""
Tools — MCP tool implementations.

Each tool has its own module with the implementation logic.
server.py provides thin @mcp.tool() wrappers that call into these.

Verb model (3 tools):
- search: Unified discovery across Drive/Gmail
- fetch: Content to filesystem (with open comments included automatically)
- create: Markdown → Doc/Sheet/Slides
"""

from .search import do_search
from .fetch import do_fetch
from .create import do_create

__all__ = ["do_search", "do_fetch", "do_create"]
