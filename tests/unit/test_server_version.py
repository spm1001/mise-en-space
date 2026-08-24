"""serverInfo.version carries the suite version (mise-vubeku).

MCPServer(version=) round-trips to initialize's serverInfo on the wire
(probed 2026-08-24, huwubi probes). mise sent '' on its first v2 releases,
so a stale long-running session was undiagnosable from the client side —
the bds-sawalu gap. This pins the wiring, not the wire: the version reaches
the constructor and matches the plugin manifest sitting beside server.py.
"""

import json
from pathlib import Path


def test_server_version_matches_plugin_manifest() -> None:
    import server

    manifest_path = (
        Path(server.__file__).resolve().parent / ".claude-plugin" / "plugin.json"
    )
    manifest = json.loads(manifest_path.read_text())
    assert server.mcp.version == manifest["version"]
    assert server.mcp.version != ""
