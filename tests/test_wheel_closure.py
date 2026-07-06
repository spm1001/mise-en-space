"""The wheel must ship the full import closure for library consumers.

glaneur (and any future consumer) imports mise adapters/extractors from an
installed wheel — no repo on sys.path. Flat-layout root modules only ship if
named in [tool.hatch.build.targets.wheel.force-include], so a new root-module
import in packaged code silently breaks external installs (the pre-2026-07-06
state: cues_util, filters, html_convert, token_store, validation were all
imported but unshipped — mise-dopufo). This test closes that gap statically.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_IMPORT_RE = re.compile(r"^(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def _wheel_config() -> tuple[set[str], set[str]]:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    wheel = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]
    packages = {p.rstrip("/") for p in wheel.get("packages", [])}
    forced = {Path(k).stem for k in wheel.get("force-include", {})}
    return packages, forced


def _root_modules() -> set[str]:
    return {p.stem for p in ROOT.glob("*.py")}


def test_shipped_code_imports_only_shipped_root_modules() -> None:
    packages, forced = _wheel_config()
    root_mods = _root_modules()
    shipped_files: list[Path] = []
    for pkg in packages:
        shipped_files.extend((ROOT / pkg).rglob("*.py"))
    shipped_files.extend(ROOT / f"{m}.py" for m in forced)

    missing: dict[str, set[str]] = {}
    for f in shipped_files:
        if any(part in {"tests", "__pycache__"} for part in f.parts):
            continue
        for mod in _IMPORT_RE.findall(f.read_text()):
            if mod in root_mods and mod not in forced and mod not in packages:
                missing.setdefault(mod, set()).add(str(f.relative_to(ROOT)))

    assert not missing, (
        "root modules imported by shipped code but absent from the wheel "
        f"force-include: {missing} — add them to "
        "[tool.hatch.build.targets.wheel.force-include] in pyproject.toml"
    )
