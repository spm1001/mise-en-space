"""
Tool Documentation Resources

Generates mise://tools/* resources directly from @mcp.tool docstrings.
Single source of truth — docstrings ARE the documentation.

Architecture Note:
    This module accesses FastMCP's internal `_tool_manager._tools` structure
    because the public `list_tools()` API is async and can't easily run at
    module load time. If FastMCP internals change, `register_from_mcp()` will
    log a warning and attempt an async fallback.

    Tested against: mcp>=1.0.0 (FastMCP)
"""

import asyncio
import logging
from typing import Any, Callable

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

    def _register_from_mcp_sync(self, mcp_server: Any) -> int:
        """
        Synchronous registration using FastMCP internal API.

        WARNING: This accesses undocumented internal structure `_tool_manager._tools`.
        If FastMCP changes this structure, registration will fail silently and
        we'll fall back to async registration.

        Returns:
            Number of tools registered
        """
        count = 0
        if hasattr(mcp_server, '_tool_manager'):
            tool_manager = mcp_server._tool_manager
            if hasattr(tool_manager, '_tools'):
                for name, tool in tool_manager._tools.items():
                    if hasattr(tool, 'fn'):
                        self.register_tool(name, tool.fn)
                        count += 1
        return count

    def _register_from_mcp_async(self, mcp_server: Any) -> int:
        """
        Async fallback using public FastMCP API.

        Uses `mcp_server.list_tools()` which is the official public API.
        Called only if sync registration fails.

        Returns:
            Number of tools registered
        """
        async def _async_register() -> int:
            count = 0
            try:
                tools = await mcp_server.list_tools()
                for tool in tools:
                    # The public API gives us Tool objects with name and description
                    # but not the original function. We create a stub that returns
                    # the description as its docstring.
                    name = tool.name
                    description = tool.description or ""

                    # Create a stub function with the docstring
                    def make_stub(doc: str) -> Callable[[], None]:
                        def stub() -> None:
                            pass
                        stub.__doc__ = doc
                        return stub

                    self.register_tool(name, make_stub(description))
                    count += 1
            except Exception as e:
                logger.error(f"Async tool registration failed: {e}")
            return count

        # Run the async function
        try:
            # Check if there's already a running loop
            try:
                asyncio.get_running_loop()
                # Can't run async in already-running loop without nest_asyncio
                logger.warning("Cannot run async fallback: event loop already running")
                return 0
            except RuntimeError:
                # No running loop - safe to use asyncio.run()
                return asyncio.run(_async_register())
        except Exception as e:
            logger.error(f"Async fallback failed: {e}")
            return 0

    def register_from_mcp(self, mcp_server: Any) -> None:
        """
        Register all tools from a FastMCP server instance.

        Attempts sync registration first (faster, uses internal API).
        Falls back to async registration if sync fails.
        Logs warning if no tools are registered.

        Args:
            mcp_server: FastMCP instance with registered tools
        """
        # Try sync registration first (faster)
        count = self._register_from_mcp_sync(mcp_server)

        if count == 0:
            # Sync failed - try async fallback
            logger.warning(
                "Sync tool registration found 0 tools. "
                "FastMCP internal API may have changed. Trying async fallback..."
            )
            count = self._register_from_mcp_async(mcp_server)

        if count == 0:
            logger.warning(
                "Tool resource registry is empty after registration. "
                "mise://tools/* resources will not be available. "
                "This may indicate a FastMCP API change - check tools.py"
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
