"""hooks/ensure-mise.sh step 2c: the kit records where its engine lives (bds-sovabu).

Ring kits (mit@mit, later family@family) ship no engine; their launcher reads
${XDG_DATA_HOME}/mise-en-space/engine and execs <root>/.venv/bin/python
<root>/server.py. The pointer must name an engine uv itself calls complete, move
forward only (a pre-release ranks below its release), and repair itself when its
target is gone. Each case runs the real hook, with the real uv, against a
throwaway kit whose project has one extra dependency: a local wheel, so nothing
touches the network.
"""

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "ensure-mise.sh"

PYPROJECT = """\
[project]
name = "engine-stub"
version = "0"
requires-python = ">=3.9"
dependencies = []

[project.optional-dependencies]
extraction = ["depx"]

[tool.uv]
package = false

[tool.uv.sources]
depx = { path = "../../wheels/depx-0.1-py3-none-any.whl" }
"""

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")


def _wheel(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    info = "depx-0.1.dist-info"
    with zipfile.ZipFile(dest / "depx-0.1-py3-none-any.whl", "w") as z:
        z.writestr("depx/__init__.py", "")
        z.writestr(f"{info}/METADATA", "Metadata-Version: 2.1\nName: depx\nVersion: 0.1\n")
        z.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        z.writestr(f"{info}/RECORD", "")


@pytest.fixture
def kits(tmp_path: Path):
    _wheel(tmp_path / "wheels")
    lock: list[bytes] = []

    def make(name: str, version: str, pyproject: str = PYPROJECT) -> Path:
        comp = tmp_path / name / "mise"
        (comp / "hooks").mkdir(parents=True)
        (tmp_path / name / ".claude-plugin").mkdir()
        (tmp_path / name / ".claude-plugin" / "plugin.json").write_text(f'{{\n  "version": "{version}"\n}}\n')
        shutil.copy(HOOK, comp / "hooks" / "ensure-mise.sh")
        (comp / "server.py").write_text("")
        (comp / "pyproject.toml").write_text(pyproject)
        if pyproject == PYPROJECT:
            if not lock:
                subprocess.run(["uv", "lock", "--offline", "--quiet", "--project", str(comp)], check=True)
                lock.append((comp / "uv.lock").read_bytes())
            (comp / "uv.lock").write_bytes(lock[0])
        return comp

    return make


def _run(tmp: Path, comp: Path) -> None:
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(comp), "CLAUDE_CONFIG_DIR": str(tmp / "config"),
           "XDG_DATA_HOME": str(tmp / "xdg"), "UV_OFFLINE": "1"}
    subprocess.run(["bash", str(comp / "hooks" / "ensure-mise.sh")], env=env, cwd=tmp,
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=180, check=True)


def _pointer(tmp: Path) -> dict[str, str]:
    f = tmp / "xdg" / "mise-en-space" / "engine"
    if not f.exists():
        return {}
    return dict(line.split("=", 1) for line in f.read_text().splitlines())


def test_pointer_moves_forward_and_repairs(tmp_path: Path, kits) -> None:
    a, b, c = kits("a", "2.0.2"), kits("b", "2.0.1"), kits("c", "2.0.3")
    _run(tmp_path, a)
    assert _pointer(tmp_path) == {"version": "2.0.2", "root": str(a)}
    _run(tmp_path, b)  # an older session must not pull ring kits back
    assert _pointer(tmp_path)["root"] == str(a)
    _run(tmp_path, c)
    assert _pointer(tmp_path) == {"version": "2.0.3", "root": str(c)}
    shutil.rmtree(c.parent)  # Claude Code pruned that version
    _run(tmp_path, a)
    assert _pointer(tmp_path)["root"] == str(a)


def test_a_pre_release_ranks_below_its_release(tmp_path: Path, kits) -> None:
    canary, release = kits("canary", "2.1.0-canary.1"), kits("release", "2.1.0")
    _run(tmp_path, canary)
    _run(tmp_path, release)
    assert _pointer(tmp_path) == {"version": "2.1.0", "root": str(release)}


def test_a_broken_env_is_resynced_before_it_is_advertised(tmp_path: Path, kits) -> None:
    # The essayeur's repro: an interrupted sync left one package missing (folder
    # and dist-info) while the rest looked fine. uv's own --check must catch it, and the hook must
    # repair the env before pointing a ring kit at it.
    a = kits("a", "2.0.2")
    _run(tmp_path, a)
    for pkg in (a / ".venv" / "lib").glob("python*/site-packages/depx*"):  # package and dist-info
        shutil.rmtree(pkg)
    (tmp_path / "xdg" / "mise-en-space" / "engine").unlink()
    _run(tmp_path, a)
    assert list((a / ".venv" / "lib").glob("python*/site-packages/depx")), "hook did not repair the env"
    assert _pointer(tmp_path)["root"] == str(a)


def test_no_pointer_when_the_sync_fails(tmp_path: Path, kits) -> None:
    broken = PYPROJECT.replace("depx-0.1-py3-none-any.whl", "missing-0.1-py3-none-any.whl")
    _run(tmp_path, kits("a", "2.0.2", pyproject=broken))
    assert _pointer(tmp_path) == {}
