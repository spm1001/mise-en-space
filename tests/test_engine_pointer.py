"""hooks/ensure-mise.sh step 2c: the kit records where its engine lives (bds-sovabu).

Ring kits (mit@mit, later family@family) ship no engine; their launcher reads
${XDG_DATA_HOME}/mise-en-space/engine and execs <root>/.venv/bin/python
<root>/server.py. The pointer must name an engine uv itself calls complete, move
forward only (a pre-release ranks below its release), and repair itself when its
target is gone. Each case runs the real hook, with the real uv, against a
throwaway kit whose project has one extra dependency: a local wheel, so nothing
touches the network.
"""

import json
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


# --- The MIT switch-over advice (bds-jasuha) -------------------------------------
# The batterie kit ships no OAuth client, so its hook says nothing about tokens —
# except to someone who WAS signed in to batterie's old ITV server and has no
# mit@mit yet: they have just lost their Google tools and need the three lines.

def _hook_output(tmp: Path, comp: Path) -> str:
    home = tmp / "home"
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(comp), "HOME": str(home),
           "CLAUDE_CONFIG_DIR": str(home / ".claude"), "XDG_DATA_HOME": str(tmp / "xdg"),
           "UV_OFFLINE": "1", "PATH": "/usr/bin:/bin:" + os.path.dirname(shutil.which("uv"))}
    env.pop("MISE_EN_SPACE_OAUTH_CLIENT", None)
    r = subprocess.run(["bash", str(comp / "hooks" / "ensure-mise.sh")], env=env, cwd=tmp,
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180, check=True)
    return r.stdout


def _pre_switch_token(tmp: Path) -> None:
    d = tmp / "home" / ".claude" / "plugins" / "data" / "mise-batterie-de-savoir"
    d.mkdir(parents=True)
    (d / "token.json").write_text("{}")


def _register(tmp: Path, *ids: str) -> None:
    reg = tmp / "home" / ".claude" / "plugins" / "installed_plugins.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text('{"plugins": {' + ", ".join(f'"{i}": []' for i in ids) + "}}")


def _register_mit(tmp: Path, *, server: bool, extra: tuple = ("batterie@batterie",)) -> None:
    """mit@mit at a real installPath; server=True gives it mise/launch.sh (post-switch)."""
    root = tmp / "cache" / "mit"
    (root / "mise").mkdir(parents=True, exist_ok=True)
    if server:
        (root / "mise" / "launch.sh").write_text("#!/bin/sh\n")
    reg = tmp / "home" / ".claude" / "plugins" / "installed_plugins.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    plugins = {i: [] for i in extra}
    plugins["mit@mit"] = [{"scope": "user", "installPath": str(root)}]
    reg.write_text(json.dumps({"plugins": plugins}))


def test_advises_the_mit_kit_to_someone_signed_in_before_the_switch(tmp_path: Path, kits) -> None:
    comp = kits("a", "2.0.7")
    _pre_switch_token(tmp_path)
    _register(tmp_path, "batterie@batterie", "commons@mit")
    out = _hook_output(tmp_path, comp)
    assert "claude plugin install mit@mit --scope user" in out
    assert "claude plugin marketplace update mit" in out
    # On screen as well as in Claude's context (rehearsal, 29 Sep: context only is invisible).
    assert "claude plugin install mit@mit --scope user" in json.loads(out)["systemMessage"]
    # Scoped claim: adoption is proven on Linux file stores, not the macOS Keychain.
    assert "nothing asks you to consent" not in out and "Usually your Google sign-in carries over" in out


def test_silent_once_mit_is_installed(tmp_path: Path, kits) -> None:
    comp = kits("a", "2.0.7")
    _pre_switch_token(tmp_path)
    _register_mit(tmp_path, server=True)
    out = _hook_output(tmp_path, comp)
    assert "mit@mit" not in out and "systemMessage" not in out


def test_silent_for_someone_never_signed_in(tmp_path: Path, kits) -> None:
    comp = kits("a", "2.0.7")
    _register(tmp_path, "batterie@batterie")
    out = _hook_output(tmp_path, comp)
    assert "mit@mit" not in out and "OAuth token" not in out


# An engine fault with no client of this kit's own: say who it breaks, and only
# call it blocking when a kit that runs the engine (mit@mit) is installed.
def _unwritable_pointer(tmp: Path) -> None:
    (tmp / "xdg").mkdir(exist_ok=True)
    (tmp / "xdg" / "mise-en-space").write_text("a file where the pointer's directory should be")


def test_pointer_fault_blocks_when_mit_runs_the_engine(tmp_path: Path, kits) -> None:
    comp = kits("a", "2.0.7")
    _register_mit(tmp_path, server=True)
    _unwritable_pointer(tmp_path)
    ctx = json.loads(_hook_output(tmp_path, comp))["hookSpecificOutput"]["additionalContext"]
    assert "needs setup" in ctx and "MIT kit's Google server will say" in ctx
    assert "nothing is broken" not in ctx


def test_pointer_fault_without_mit_is_a_neutral_note(tmp_path: Path, kits) -> None:
    comp = kits("a", "2.0.7")
    _register(tmp_path, "batterie@batterie")
    _unwritable_pointer(tmp_path)
    ctx = json.loads(_hook_output(tmp_path, comp))["hookSpecificOutput"]["additionalContext"]
    assert "No kit here runs the engine yet" in ctx and "Nothing in this session depends" in ctx
    assert "mit@mit" not in ctx  # no ITV hypothetical on a machine without mit (D3)
    assert "nothing is broken" not in ctx and "MIT kit's" not in ctx



# Round 4 (essayeur, 29 Sep): registered is not the same as carrying a server.
def test_a_serverless_mit_gets_the_update_advice_on_screen(tmp_path: Path, kits) -> None:
    comp = kits("a", "2.0.7")
    _pre_switch_token(tmp_path)
    _register_mit(tmp_path, server=False)
    sm = json.loads(_hook_output(tmp_path, comp))["systemMessage"]
    assert "claude plugin update mit@mit" in sm and "claude plugin marketplace update mit" in sm


def test_an_older_intact_engine_keeps_mit_running_through_a_failed_sync(tmp_path: Path, kits) -> None:
    old, new = kits("a", "2.0.6"), kits("b", "2.0.7")
    _register_mit(tmp_path, server=True)
    _hook_output(tmp_path, old)                  # records 2.0.6
    shutil.rmtree(tmp_path / "wheels")           # 2.0.7 cannot sync offline now
    ctx = json.loads(_hook_output(tmp_path, new))["hookSpecificOutput"]["additionalContext"]
    assert "keeps running the engine recorded before this one (version 2.0.6)" in ctx
    assert "needs setup" not in ctx and "cannot find the engine" not in ctx


BWRAP = shutil.which("bwrap")


@pytest.mark.skipif(BWRAP is None, reason="needs bwrap to hide poppler")
def test_missing_poppler_is_said_to_degrade_mits_tools(tmp_path: Path, kits) -> None:
    comp = kits("a", "2.0.7")
    _register_mit(tmp_path, server=True)
    home = tmp_path / "home"
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(comp), "HOME": str(home),
           "CLAUDE_CONFIG_DIR": str(home / ".claude"), "XDG_DATA_HOME": str(tmp_path / "xdg"),
           "UV_OFFLINE": "1", "PATH": "/usr/bin:/bin:" + os.path.dirname(shutil.which("uv"))}
    env.pop("MISE_EN_SPACE_OAUTH_CLIENT", None)
    # An empty /usr/bin with only the tools the hook uses: no pdftotext anywhere.
    args = [BWRAP, "--bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/usr/bin"]
    for b in ("bash", "sh", "env", "sed", "grep", "mktemp", "mv", "mkdir", "sort", "tail", "head",
              "dirname", "cat", "basename", "tr", "python3", "rm", "cp", "chmod", "ls"):
        real = shutil.which(b, path="/usr/bin:/bin")
        if real:
            args += ["--ro-bind", os.path.realpath(real), f"/usr/bin/{b}"]
    r = subprocess.run(args + ["/usr/bin/bash", str(comp / "hooks" / "ensure-mise.sh")], env=env,
                       cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
    if r.returncode != 0 and "bwrap" in r.stderr:
        pytest.skip(f"bwrap unusable here: {r.stderr.strip()[:120]}")
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "PDF text extraction in the MIT kit's Google tools is degraded" in ctx
    assert "Nothing in this session depends" not in ctx
