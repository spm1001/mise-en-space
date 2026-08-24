"""
Tool Documentation Resources

Generates mise://tools/* resources from the tool text the server actually
advertises — single source of truth is the server's own registry.

Architecture Note:
    Registration rides the PUBLIC async `list_tools()` API only; the fast
    path that read the SDK's private tool registry was deleted (mise-vubeku,
    2026-08-24).
    list_tools() returns wire Tool objects (name + description, no function),
    so each tool registers a stub carrying the description as its docstring.
    For search/fetch the description IS the docstring (bare @mcp.tool());
    for do() it is the curated DO_DESCRIPTION — richer than the one-line
    docstring the old path rendered. Async-from-sync goes through
    async_bridge, the one door.
"""

import logging
from typing import Any, Callable

from async_bridge import run_async_blocking

logger = logging.getLogger(__name__)


def docstring_to_markdown(tool_name: str, docstring: str) -> str:
    """
    Convert a tool's docstring to clean markdown.

    The docstrings are already well-formatted with sections like:
    - Description
    - Args:
    - Returns:
    - Example:
    - See Also:

    We just add a title and clean up formatting.
    """
    if not docstring:
        return f"# {tool_name}()\n\nNo documentation available."

    # Clean up indentation
    lines = docstring.strip().split('\n')
    if len(lines) > 1:
        # Find minimum indentation (excluding empty lines and first line)
        indents = [len(line) - len(line.lstrip())
                   for line in lines[1:] if line.strip()]
        min_indent = min(indents) if indents else 0
        lines = [lines[0]] + [line[min_indent:] if len(line) > min_indent else line
                              for line in lines[1:]]

    cleaned = '\n'.join(lines)

    return f"# {tool_name}()\n\n{cleaned}"


class ToolResourceRegistry:
    """
    Registry for auto-generated tool documentation resources.

    Generates mise://tools/* resources from tool docstrings.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}
        self._cache: dict[str, dict[str, str]] = {}

    def register_tool(self, name: str, func: Callable[..., Any]) -> None:
        """Register a tool for documentation generation."""
        self._tools[name] = func
        # Clear cache for this tool
        uri = f"mise://tools/{name}"
        if uri in self._cache:
            del self._cache[uri]

    def register_from_mcp(self, mcp_server: Any) -> None:
        """
        Register every tool the server advertises, via public list_tools().

        The wire Tool objects carry name + description but not the original
        function, so each registers a stub whose docstring is the description.
        Logs a warning when registration comes back empty — mise://tools/*
        would otherwise be silently absent.

        Args:
            mcp_server: MCPServer instance with registered tools
        """
        async def _register_all() -> int:
            count = 0
            for tool in await mcp_server.list_tools():
                def make_stub(doc: str) -> Callable[[], None]:
                    def stub() -> None:
                        pass
                    stub.__doc__ = doc
                    return stub

                self.register_tool(tool.name, make_stub(tool.description or ""))
                count += 1
            return count

        # run_async_blocking handles the already-running-loop case by thread.
        try:
            count = run_async_blocking(_register_all())
        except Exception as e:
            logger.error(f"Tool registration via list_tools() failed: {e}")
            count = 0

        if count == 0:
            logger.warning(
                "Tool resource registry is empty after registration. "
                "mise://tools/* resources will not be available. "
                "This may indicate an MCP SDK API change - check tools.py"
            )
        else:
            logger.info(f"Tool resource registry: {count} tools registered for mise://tools/* documentation")

            # Warn about tools with empty docstrings
            empty_docstring_tools = [
                name for name, func in self._tools.items()
                if not (func.__doc__ or "").strip()
            ]
            if empty_docstring_tools:
                logger.warning(
                    f"Tools with empty docstrings ({len(empty_docstring_tools)}): "
                    f"{', '.join(sorted(empty_docstring_tools))}. "
                    "These will show 'No documentation available' in mise://tools/* resources."
                )

    def get_tool_names(self) -> set[str]:
        """Get set of all registered tool names."""
        return set(self._tools.keys())

    def get_resource(self, uri: str) -> dict[str, str]:
        """
        Get resource by URI.

        Args:
            uri: Resource URI (e.g., "mise://tools/fetch")

        Returns:
            Resource dict with uri, mimeType, text

        Raises:
            KeyError: If tool not found
        """
        if uri in self._cache:
            return self._cache[uri]

        # Parse tool name from URI
        if not uri.startswith("mise://tools/"):
            raise KeyError(f"Not a tool resource: {uri}")

        tool_name = uri.split("/")[-1]

        if tool_name not in self._tools:
            raise KeyError(f"Tool not found: {tool_name}")

        func = self._tools[tool_name]
        docstring = func.__doc__ or ""
        markdown = docstring_to_markdown(tool_name, docstring)

        resource = {
            "uri": uri,
            "mimeType": "text/markdown",
            "text": markdown
        }
        self._cache[uri] = resource
        return resource

    def list_resources(self) -> list[dict[str, str]]:
        """List all available tool resources."""
        resources: list[dict[str, str]] = []
        for name in sorted(self._tools.keys()):
            func = self._tools[name]
            docstring = func.__doc__ or ""
            # First line of docstring as description
            first_line = docstring.strip().split('\n')[0] if docstring else "No description"
            resources.append({
                "uri": f"mise://tools/{name}",
                "name": name,
                "description": first_line[:100]
            })
        return resources


# Global registry instance
_registry = ToolResourceRegistry()


def get_tool_registry() -> ToolResourceRegistry:
    """Get the global tool resource registry."""
    return _registry


def get_tool_resource(uri: str) -> dict[str, str]:
    """Get a tool resource by URI."""
    return _registry.get_resource(uri)


def list_tool_resources() -> list[dict[str, str]]:
    """List all tool resources."""
    return _registry.list_resources()
