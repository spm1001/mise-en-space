"""
Tools — MCP tool implementations.

Each tool has its own module with the implementation logic.
server.py provides thin @mcp.tool() wrappers that call into these.
"""

from .search import do_search
from .fetch import do_fetch

__all__ = ["do_search", "do_fetch"]
