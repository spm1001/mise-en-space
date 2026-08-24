"""The 2048-char MCP description ceiling, mechanically enforced.

Over the limit, Claude Code slices the description AND the Anthropic API
silently drops properties from the tool's JSON schema during tool_reference
expansion. do() has 26 properties, so the failure is both invisible and
severe: a caller emits a param, the API strips it before CC sees the tool_use,
and the operation runs with the param missing.

.bon/understanding.md has documented this since the mise-jimohe shrink. Nothing
enforced it, which is that same file's "mass accumulates where mechanical
enforcement can't see". Adding copy took do() to 2036/2048 — twelve characters —
and only a manual measurement caught it.
"""

import json

import pytest

MCP_DESCRIPTION_CEILING = 2048

# Fail while there is still room to think. A test that only guards the hard
# ceiling lets the description creep to 2047, and then the next person to add
# an operation ships silent property loss instead of a red build.
SAFE_HEADROOM = 100


@pytest.fixture(scope="module")
def tools():
    import server
    from async_bridge import run_async_blocking

    # Public API only (mise-vubeku): what list_tools() advertises IS what the
    # ceiling applies to — measuring the wire objects measures the real thing.
    return {t.name: t for t in run_async_blocking(server.mcp.list_tools())}


@pytest.mark.parametrize("name", ["search", "fetch", "do"])
def test_description_under_hard_ceiling(tools, name: str) -> None:
    desc = tools[name].description or ""
    assert len(desc) < MCP_DESCRIPTION_CEILING, (
        f"{name}() description is {len(desc)} chars, over the {MCP_DESCRIPTION_CEILING} "
        "ceiling. Over this line the API drops schema properties SILENTLY — move "
        "detail into an MCP resource (mise://docs/*) rather than trimming meaning."
    )


@pytest.mark.parametrize("name", ["search", "fetch", "do"])
def test_description_keeps_working_headroom(tools, name: str) -> None:
    desc = tools[name].description or ""
    headroom = MCP_DESCRIPTION_CEILING - len(desc)
    assert headroom >= SAFE_HEADROOM, (
        f"{name}() has only {headroom} chars of headroom. Trim before adding — "
        "the next addition would land you at the ceiling, where failures are silent."
    )


def test_do_declares_every_operation_it_dispatches(tools) -> None:
    """The description is an advert; a mismatch sends callers at a dead end
    (mise-bacodi shipped exactly that for 'slides' for six months)."""
    from tools import OPERATIONS
    desc = tools["do"].description or ""
    missing = sorted(op for op in OPERATIONS if op not in desc)
    assert not missing, f"operations dispatched but not advertised: {missing}"
