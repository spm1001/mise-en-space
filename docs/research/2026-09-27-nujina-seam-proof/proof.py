"""Throwaway-home proof for mise-nujina: one engine, two Workspaces, clients from outside.

The engine at $VH/engine ships NO credentials.json. Each server is spawned
through `env -i` with HOME=$VH (a claude-vanilla world) plus only the kit
wiring: MISE_EN_SPACE_OAUTH_CLIENT and MISE_EN_SPACE_DATA_DIR. The pre-seam tokens sit at the
old per-flavour locations inside $VH; the kit data dirs start empty.
"""

import asyncio
import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

VH = Path("/var/tmp/claude-vanilla-modha/nujina")
ENGINE = VH / "engine"
PY = ENGINE / ".venv" / "bin" / "python3"
DATA = VH / ".claude" / "plugins" / "data"
PATH = "/home/modha/.local/bin:/usr/local/bin:/usr/bin:/bin"
KITS = {
    "mise": {"client": VH / "kits/mit/oauth-client.json", "data": DATA / "kit-mit",
             "domain": "itv.com", "legacy": DATA / "mise-batterie-de-savoir"},
    "mise-home": {"client": VH / "kits/family/oauth-client.json", "data": DATA / "kit-family",
                  "domain": "planetmodha.com", "legacy": DATA / "mise-home"},
}
results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def client_id(p: Path) -> str:
    return json.loads(p.read_text())["installed"]["client_id"]


def env_args(extra: dict[str, str]) -> list[str]:
    base = {"HOME": str(VH), "PATH": PATH, "USER": "modha", "LANG": "C.UTF-8"}
    return ["-i", *[f"{k}={v}" for k, v in {**base, **extra}.items()]]


def text_of(result) -> dict:
    raw = "".join(getattr(b, "text", "") for b in result.content)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}


async def call(extra_env: dict[str, str], calls: list[tuple[str, dict]]) -> list[dict]:
    params = StdioServerParameters(
        command="/usr/bin/env", args=[*env_args(extra_env), str(PY), str(ENGINE / "server.py")])
    out = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for tool, args in calls:
                out.append(text_of(await session.call_tool(tool, args)))
    return out


async def main() -> int:
    legacy_before = {n: sha(k["legacy"] / "token.json") for n, k in KITS.items()}

    # 0. Control: the engine alone, no kit wiring, is not configured.
    [r] = await call({}, [("do", {"operation": "setup_oauth", "base_path": str(VH / "work")})])
    check("engine with no wiring refuses to sign in (ships no client)",
          r.get("error") is True and "no OAuth client configured" in r.get("message", ""),
          r.get("message", "")[:90])

    # 1. Each kit: setup_oauth says already signed in, then a live Drive search answers.
    for name, kit in KITS.items():
        wiring = {"MISE_EN_SPACE_OAUTH_CLIENT": str(kit["client"]), "MISE_EN_SPACE_DATA_DIR": str(kit["data"])}
        setup, search = await call(wiring, [
            ("do", {"operation": "setup_oauth", "base_path": str(VH / "work")}),
            ("search", {"sources": ["drive"], "type": "doc", "max_results": 2,
                        "base_path": str(VH / "work")}),
        ])
        check(f"{name}: setup_oauth finds the adopted token — no consent",
              setup.get("status") == "already_authenticated", setup.get("status") or setup.get("message", "")[:90])
        email = ((search.get("cues") or {}).get("_identity") or {}).get("email", "")
        check(f"{name}: live Drive search answers as the {kit['domain']} identity",
              not search.get("error") and email.endswith("@" + kit["domain"]),
              f"identity={email.split('@')[-1] or 'NONE'} drive_count={search.get('drive_count')} "
              f"error={search.get('message', '')[:80] if search.get('error') else None}")
        adopted = kit["data"] / "token.json"
        check(f"{name}: token adopted into the kit store, 0600, minted by the kit's client",
              adopted.is_file() and stat.S_IMODE(adopted.stat().st_mode) == 0o600
              and json.loads(adopted.read_text())["client_id"] == client_id(kit["client"]),
              str(adopted.relative_to(VH)) if adopted.exists() else "absent")
        check(f"{name}: pre-seam store left byte-identical",
              sha(kit["legacy"] / "token.json") == legacy_before[name])

    # 2. Control: cross-wired kit (family client over the MIT store) refuses.
    [r] = await call(
        {"MISE_EN_SPACE_OAUTH_CLIENT": str(KITS["mise-home"]["client"]), "MISE_EN_SPACE_DATA_DIR": str(KITS["mise"]["data"])},
        [("search", {"sources": ["drive"], "type": "doc", "max_results": 1, "base_path": str(VH / "work")})])
    blob = json.dumps(r)
    check("cross-wired kit (family client, MIT store) refuses to act as the other Workspace",
          "two different Workspace identities" in blob, blob[:140])

    # 3. The CLI's consent URL carries the SUPPLIED client (print mode: no listener, not clicked).
    for name, kit in KITS.items():
        cli_store = VH / "tmp" / f"cli-{name}"
        p = subprocess.run(
            ["/usr/bin/env", *env_args({"MISE_EN_SPACE_OAUTH_CLIENT": str(kit["client"]), "MISE_EN_SPACE_DATA_DIR": str(cli_store)}),
             str(PY), "-m", "auth"], cwd=ENGINE, capture_output=True, text=True, timeout=60)
        url = next((ln.strip() for ln in p.stdout.splitlines() if ln.strip().startswith("https://accounts.google.com")), "")
        got = parse_qs(urlparse(url).query).get("client_id", [""])[0]
        check(f"{name}: CLI consent URL names the kit's client",
              got == client_id(kit["client"]), f"client {got.split('-')[0] or 'NONE'}")

    # 4. The SessionStart hook: quiet when unconfigured; quiet when the store is
    #    empty but the flavour's own pre-seam token is there to adopt; nags when
    #    a client is wired and there is no token anywhere (a home with none).
    def hook(extra: dict[str, str], home: Path = VH) -> str:
        args = [a if not a.startswith("HOME=") else f"HOME={home}" for a in env_args({})]
        args += [f"{k}={v}" for k, v in {"CLAUDE_PLUGIN_ROOT": str(ENGINE), **extra}.items()]
        p = subprocess.run(["/usr/bin/env", *args, "bash", str(ENGINE / "hooks/ensure-mise.sh")],
                           capture_output=True, text=True, timeout=120)
        return p.stdout + p.stderr
    wired = {"MISE_EN_SPACE_OAUTH_CLIENT": str(KITS["mise"]["client"]),
             "MISE_EN_SPACE_DATA_DIR": str(VH / "tmp" / "empty-store")}
    quiet = hook({})
    check("hook is silent for an engine with no client", "OAuth token" not in quiet, quiet.strip()[:120] or "(no output)")
    adoptable = hook(wired)
    check("hook is silent when the seam store is empty but the own pre-seam token will be adopted",
          "OAuth token" not in adoptable, adoptable.strip()[:120] or "(no output)")
    bare_home = VH / "tmp" / "home-with-no-token"
    bare_home.mkdir(parents=True, exist_ok=True)
    loud = hook(wired, home=bare_home)
    check("control: hook nags once a client is wired and no token exists anywhere", "OAuth token" in loud, loud.strip()[:120])

    failed = [label for label, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
