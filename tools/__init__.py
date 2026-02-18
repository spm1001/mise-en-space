"""
Tools — MCP tool implementations.

Each tool has its own module with the implementation logic.
server.py provides thin @mcp.tool() wrappers that call into these.

Verb model (3 tools):
- search: Unified discovery across Drive/Gmail
- fetch: Content to filesystem (with open comments included automatically)
- do: Act on Workspace (create, move, rename, edit)
"""

from .search import do_search
from .fetch import do_fetch
from .create import do_create
from .move import do_move
from .overwrite import do_overwrite

__all__ = ["do_search", "do_fetch", "do_create", "do_move", "do_overwrite"]
