"""Both CI-reachable consumer doors are walked as artefacts — mise-wahane.

mise has exactly two product doors CI can reach, and every regular test
enters through neither: the repo sits on sys.path, so the suite stands
where no consumer ever stands. Both wheel burns shipped under a fully
green suite that way (mise-dopufo: five unshipped root modules;
mise-ditoja: missing config/attachment_filters.json — glaneur.service,
nightly, three nights running).

LIBRARY DOOR (test_library_door_*): build the wheel, install it
core-only into a clean venv, import every module the wheel ships from a
NEUTRAL cwd, then make one behavioural call. Catches what the static
closure test (tests/test_wheel_closure.py) structurally cannot:
extras-only third-party deps imported at module level (the slim/
Cornichon breakage class), missing data files of any future kind, and
dependency-declaration gaps.

MCP DOOR (TestMcpDoor): spawn server.py over real stdio and walk the
credential-free subset — tools/list, do()'s advertised ops, and the
pure-validation refusals. The live-credential cases stay in
scripts/smoke_stdio.py, hand-run (the schedule question is mise-pirusu).

The THIRD door — the marketplace-installed plugin — is out of CI's reach
by construction: the assembler and flavour transform sit between this
repo and what users install. That stays the post-publish re-verify habit
(bds-sawalu).
"""

import json
import os
import shutil
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# jeton is a [tool.uv.sources] git dependency. Source maps do NOT ride
# wheel metadata — the built wheel's METADATA says bare `Requires-Dist:
# jeton` — so every wheel consumer must map the source themselves
# (glaneur does; the worked example for library consumers must say so).
# The clean-venv install below does what a real consumer does.
_JETON_SPEC = "jeton @ git+https://github.com/spm1001/jeton.git"


def _wheel_modules(wheel: Path) -> list[str]:
    """Importable module names derived from the ARTEFACT, not pyproject.

    Whatever .py files the wheel actually ships is what a consumer can
    reach — deriving from config would re-trust the thing under test.
    """
    mods = set()
    for name in zipfile.ZipFile(wheel).namelist():
        if not name.endswith(".py") or ".dist-info" in name:
            continue
        mods.add(name[:-3].replace("/", ".").removesuffix(".__init__"))
    return sorted(mods)


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv to build/install")
def test_library_door_clean_venv_core_only(tmp_path):
    subprocess.run(
        ["uv", "build", "--wheel", "-o", str(tmp_path)],
        cwd=REPO, check=True, capture_output=True, timeout=120,
    )
    (wheel,) = tmp_path.glob("*.whl")

    modules = _wheel_modules(wheel)
    # Known-positive control on the enumerator itself: a sweep over an
    # accidentally-empty module list would pass vacuously. Doubles as the
    # fast tripwire for a lost force-include line (control-proven: dropping
    # "filters.py" reds here before any venv is built).
    for known_shipped in ("filters", "adapters.drive", "tools"):
        assert known_shipped in modules, (
            f"LIBRARY DOOR: {known_shipped!r} is missing from the built wheel "
            f"— a [tool.hatch.build.targets.wheel] entry has been lost from "
            f"pyproject.toml (the mise-dopufo/mise-ditoja class)"
        )

    venv = tmp_path / "venv"
    subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(venv)],
        check=True, capture_output=True, timeout=120,
    )
    venv_python = venv / "bin" / "python"
    subprocess.run(
        ["uv", "pip", "install", "--python", str(venv_python), str(wheel), _JETON_SPEC],
        check=True, capture_output=True, timeout=600,
    )

    sweep = textwrap.dedent(
        f"""
        import importlib, json
        failures = {{}}
        for mod in {modules!r}:
            try:
                importlib.import_module(mod)
            except Exception as e:
                failures[mod] = f"{{type(e).__name__}}: {{e}}"
        result = {{"failures": failures, "survivors": None, "filters_file": None}}
        if "filters" not in failures:
            import filters
            result["filters_file"] = filters.__file__
            result["survivors"] = len(filters.filter_attachments([{{
                "filename": "contract.pdf",
                "mime_type": "application/pdf",
                "size": 900_000,
            }}]))
        print(json.dumps(result))
        """
    )
    # NEUTRAL cwd is load-bearing: sys.path[0] inherits the cwd, so a
    # repo-cwd sweep resolves the working tree and proves nothing about
    # the install (the 2026-08-10 false green).
    out = subprocess.run(
        [str(venv_python), "-c", sweep],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, f"LIBRARY DOOR: sweep interpreter died:\n{out.stderr}"
    report = json.loads(out.stdout)

    assert not report["failures"], (
        "LIBRARY DOOR: modules the wheel ships failed to import core-only "
        f"in a clean venv: {report['failures']} — an extras-only dep at "
        "module level, a lost force-include line, or an undeclared dependency"
    )
    # The behavioural call the ditoja incident actually killed.
    assert report["survivors"] == 1, (
        f"LIBRARY DOOR: filter_attachments misbehaved from the wheel: {report}"
    )
    # And prove the sweep exercised the INSTALL, not this working tree.
    assert str(REPO) not in report["filters_file"], (
        f"LIBRARY DOOR: sweep resolved {report['filters_file']} — the working "
        "tree leaked onto the probe's sys.path; the walk proved nothing"
    )


# The refusal cases the MCP walk sends through the wire. Both work with NO
# credential reachable: the Chat link is pure pre-flight validation; the
# msg-a Show-original refusal ATTEMPTS its candidates search, which fails
# open on the absent token — so the hermetic env is also a live proof that
# fail-open holds (the refusal must arrive intact regardless).
_REFUSAL_CASES = [
    (
        "https://mail.google.com/chat/u/0/#chat/space/AAQAXm1PxYs",
        ["Google Chat link"],
    ),
    (
        "https://mail.google.com/mail/u/0/"
        "?ik=2bb48b24a5&view=om&permmsgid=msg-a:r-8125895545114462359",
        ["msg-a", "Message-ID"],
    ),
]


async def test_mcp_door_stdio_credential_free_walk(tmp_path):
    """One stdio session, the whole credential-free walk.

    Deliberately ONE test rather than a session fixture: stdio_client's
    anyio cancel scopes must enter and exit in the same task, and a
    pytest-asyncio yield-fixture tears down in a different one (measured
    here 2026-08-12 — all four split tests passed, all four errored on
    teardown with 'Attempted to exit cancel scope in a different task').
    Each assert carries its own MCP DOOR label instead.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    # Hermetic: a dev machine's personal token must be unreachable (guest
    # mode's override path is authoritative — no Keychain fallback), and
    # CDP endpoints are cleared because tube has a live browser on :9223.
    env["MISE_TOKEN_PATH"] = str(tmp_path / "deliberately-absent.json")
    env.pop("PASSE_CDP", None)
    env.pop("MISE_CDP_ENDPOINT", None)

    params = StdioServerParameters(
        command=sys.executable, args=[str(REPO / "server.py")], env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name: t for t in (await session.list_tools()).tools}
            assert set(tools) == {"search", "fetch", "do"}, (
                f"MCP DOOR: tool surface changed: {sorted(tools)}"
            )

            from tools import OPERATIONS  # the dispatch registry, repo-side

            desc = tools["do"].description or ""
            missing_ops = [op for op in OPERATIONS if op not in desc]
            assert not missing_ops, (
                f"MCP DOOR: ops in DISPATCH but absent from the wire "
                f"description: {missing_ops} — an unadvertised op is "
                "unreachable in practice"
            )

            for file_id, expected in _REFUSAL_CASES:
                result = await session.call_tool(
                    "fetch", {"file_id": file_id, "base_path": str(tmp_path)}
                )
                text = "".join(getattr(b, "text", "") for b in result.content)
                missing = [want for want in expected if want not in text]
                assert not missing, (
                    f"MCP DOOR: refusal for {file_id!r} lost its teaching "
                    f"text {missing}; got: {text[:400]}"
                )
                assert "deliberately-absent" not in text, (
                    f"MCP DOOR: the refusal for {file_id!r} leaked the "
                    "credential error into its payload — fail-open broke"
                )
