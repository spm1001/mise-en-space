"""hooks/ensure-mise.sh step 2c: the kit records where its engine lives (bds-sovabu).

Ring kits (mit@mit, later family@family) ship no engine; their launcher reads
${XDG_DATA_HOME}/mise-en-space/engine and execs <root>/.venv/bin/python
<root>/server.py. The pointer must name a complete engine, never move backwards
while the newer target is intact, and repair itself when its target is gone.
Each case runs the real hook from a throwaway kit copy with a stub .venv.
"""

import os
import shutil
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "ensure-mise.sh"


def _kit(tmp: Path, name: str, version: str, *, complete: bool = True) -> Path:
    comp = tmp / name / "mise"
    (comp / "hooks").mkdir(parents=True)
    (tmp / name / ".claude-plugin").mkdir()
    (tmp / name / ".claude-plugin" / "plugin.json").write_text(f'{{\n  "version": "{version}"\n}}\n')
    shutil.copy(HOOK, comp / "hooks" / "ensure-mise.sh")
    (comp / "server.py").write_text("")
    (comp / ".venv" / "bin").mkdir(parents=True)
    py = comp / ".venv" / "bin" / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    if complete:
        (comp / ".venv" / "lib" / "python3.11" / "site-packages" / "markitdown").mkdir(parents=True)
    return comp


def _run(tmp: Path, comp: Path) -> None:
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(comp), "CLAUDE_CONFIG_DIR": str(tmp / "config"),
           "XDG_DATA_HOME": str(tmp / "xdg")}
    subprocess.run(["bash", str(comp / "hooks" / "ensure-mise.sh")], env=env, cwd=tmp,
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=120, check=True)


def _pointer(tmp: Path) -> dict[str, str]:
    f = tmp / "xdg" / "mise-en-space" / "engine"
    if not f.exists():
        return {}
    return dict(line.split("=", 1) for line in f.read_text().splitlines())


def test_pointer_moves_forward_and_repairs(tmp_path: Path) -> None:
    a = _kit(tmp_path, "a", "2.0.2")
    b = _kit(tmp_path, "b", "2.0.1")
    c = _kit(tmp_path, "c", "2.0.3")
    _run(tmp_path, a)
    assert _pointer(tmp_path) == {"version": "2.0.2", "root": str(a)}
    _run(tmp_path, b)  # an older session must not pull ring kits back
    assert _pointer(tmp_path)["root"] == str(a)
    _run(tmp_path, c)
    assert _pointer(tmp_path) == {"version": "2.0.3", "root": str(c)}
    shutil.rmtree(c.parent)  # Claude Code pruned that version
    _run(tmp_path, a)
    assert _pointer(tmp_path)["root"] == str(a)


def test_no_pointer_to_an_incomplete_engine(tmp_path: Path) -> None:
    # A .venv without the extraction extra is what the old hook's plain `uv sync`
    # left; with no uv on PATH the hook cannot complete it, so it must not
    # advertise it.
    comp = _kit(tmp_path, "a", "2.0.2", complete=False)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(comp),
           "CLAUDE_CONFIG_DIR": str(tmp_path / "config"), "XDG_DATA_HOME": str(tmp_path / "xdg")}
    subprocess.run(["bash", str(comp / "hooks" / "ensure-mise.sh")], env=env, cwd=tmp_path,
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    assert _pointer(tmp_path) == {}
