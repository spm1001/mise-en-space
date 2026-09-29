# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Launch an installed Mise flavour for Codex without pinning its cache version."""
import argparse
import json
import os
from pathlib import Path
import sys

# instance -> (registry key, subdirectory holding the engine). Since the kit fold
# (bds-jakemi) the ITV flavour ships as the `mise` component of batterie@batterie,
# so its engine sits one level down; mise-home is still a plugin of its own.
PLUGINS = {"mise": ("batterie@batterie", "mise"), "mise-home": ("mise-home@batterie-home", "")}


def resolve(instance, registry):
    key, sub = PLUGINS[instance]
    data = json.loads(registry.read_text())
    entries = data.get("plugins", {}).get(key, [])
    roots = {Path(row["installPath"]).resolve() for row in entries if row.get("scope") == "user"}
    if len(roots) != 1:
        raise ValueError(f"Expected one current user installation of {key}; found {len(roots)}")
    root = roots.pop() / sub if sub else roots.pop()
    metadata = json.loads((root / ".claude-plugin/plugin.json").read_text())
    if metadata.get("name") != instance:
        raise ValueError(f"Installed plugin identity does not match requested {instance}")
    for name in ("server.py", "uv.lock", "pyproject.toml"):
        if not (root / name).is_file():
            raise ValueError(f"Incomplete {instance} installation: {name} missing")
    return root, metadata


def workspace_environment(instance, registry, home=None):
    """The OAuth-client seam for the ITV flavour (bds-jasuha).

    The batterie kit ships no OAuth client since the MIT switch-over; mit@mit
    carries ITV's, and its server runs the engine with a token store of its own
    (${CLAUDE_PLUGIN_DATA}, i.e. data/mit-mit). Point Codex at the same pair so
    both surfaces share one sign-in. No mit@mit install: add nothing, and the
    engine says plainly that no client is configured.
    """
    if instance != "mise":
        return {}
    data = json.loads(registry.read_text())
    roots = {Path(row["installPath"]).resolve()
             for row in data.get("plugins", {}).get("mit@mit", []) if row.get("scope") == "user"}
    if len(roots) != 1:
        return {}
    client = roots.pop() / "mise" / "itv-oauth-client.json"
    if not client.is_file():
        return {}
    home = Path(home) if home is not None else Path.home()
    return {"MISE_EN_SPACE_OAUTH_CLIENT": str(client),
            "MISE_EN_SPACE_DATA_DIR": str(home / ".claude/plugins/data/mit-mit")}


def launch_environment(root, original):
    environment = dict(original)
    # This server name selects the established flavour's identity. An ambient
    # caller override must not silently turn either labelled server into another account.
    for key in ("MISE_TOKEN_PATH", "MISE_CREDENTIALS"):
        environment.pop(key, None)
    environment["CLAUDE_PLUGIN_ROOT"] = str(root)
    return environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance", choices=PLUGINS)
    parser.add_argument("--describe", action="store_true")
    args = parser.parse_args()
    try:
        registry = Path.home() / ".claude/plugins/installed_plugins.json"
        root, metadata = resolve(args.instance, registry)
        seam = workspace_environment(args.instance, registry)
        description = {"instance": args.instance, "identity": metadata.get("identity"), "version": metadata.get("version"), "code_root": str(root), "oauth_client": seam.get("MISE_EN_SPACE_OAUTH_CLIENT")}
        if args.describe:
            print(json.dumps(description))
            return 0
        print("mise-codex: " + json.dumps(description), file=sys.stderr)
        command = ["uv", "run", "--project", str(root), "--frozen", "--extra", "extraction", "python3", str(root / "server.py")]
        os.execvpe(command[0], command, {**launch_environment(root, os.environ), **seam})
    except (OSError, ValueError, KeyError) as error:
        print(f"mise-codex: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
