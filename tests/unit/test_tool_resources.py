"""mise://tools/* registration rides the public list_tools() API (mise-vubeku).

The old fast path read the SDK's private tool registry and rendered each
tool's DOCSTRING — which for do() was the one-line "Act on Google
Workspace." while the curated operations text lived only in description=.
The public route registers what the server actually advertises, so
mise://tools/do now carries the full operations listing. These pin both
the registration count and that content upgrade.
"""


def test_registry_advertises_all_three_tools() -> None:
    import server  # noqa: F401 — importing runs register_from_mcp
    from resources.tools import get_tool_registry

    assert {"search", "fetch", "do"} <= get_tool_registry().get_tool_names()


def test_do_resource_carries_operations_text_not_stub_docstring() -> None:
    import server  # noqa: F401
    from resources.tools import get_tool_resource

    text = get_tool_resource("mise://tools/do")["text"]
    # The curated description names operations; the old docstring named none.
    assert "create" in text
    assert "draft" in text
