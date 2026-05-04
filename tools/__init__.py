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
from .edit import do_prepend, do_append, do_replace_text
from .draft import do_draft
from .reply_draft import do_reply_draft
from .rename import do_rename
from .share import do_share
from .gmail_ops import do_archive, do_star, do_label
from .setup_oauth import do_setup_oauth

# Single source of truth for valid do() operation names.
OPERATIONS = frozenset({
    "create", "move", "rename", "share", "overwrite", "prepend", "append", "replace_text",
    "draft", "reply_draft", "archive", "star", "label",
    "setup_oauth",
})

__all__ = [
    "do_search", "do_fetch", "do_create", "do_move", "do_rename", "do_share", "do_overwrite",
    "do_prepend", "do_append", "do_replace_text", "do_draft", "do_reply_draft",
    "do_archive", "do_star", "do_label", "do_setup_oauth", "OPERATIONS",
]
