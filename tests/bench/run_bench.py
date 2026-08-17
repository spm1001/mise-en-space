# /// script
# requires-python = ">=3.11"
# ///
"""Runner for the deposit-format bench: plan in, transcripts out.

A plan is a JSON list of runs: {"run_id", "qid", "format", "arm", "model"}.
  arm "tools"  — agentic: subject gets an isolated world dir holding the
                 deposit, a folder-pointing prompt, and full tools.
  arm "inline" — Garni-profile: the deposit's files are inlined into the
                 prompt, --tools "" — the subject can only read and answer.
                 (A subject with no tools cannot open files, so "tools-off"
                 necessarily means inline delivery; this also mirrors how the
                 unattended consumer actually receives content.)
  model "fable" — no --model flag: ANTHROPIC_MODEL rides ardoise's Vertex
                 passthrough. "opus" — --model opus (resolves via
                 ANTHROPIC_DEFAULT_OPUS_MODEL). Actual model is read back from
                 the transcript's init event by the scorer; trust that, not this.

Idempotent: a run whose transcript already holds a result event is skipped —
the ~50-minute background-kill ceiling is survived by re-invoking with the
same plan. Fixture integrity: the deposit's files are hashed before and after
every spawn; mutation aborts the run's result as contaminated.

Usage:
    uv run --script run_bench.py --plan pilot.json [--jobs 3] [--out ~/bench-work]
    uv run --script run_bench.py --make-smoke-plan   # writes smoke.json (4 runs)
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).parent
ARDOISE = HERE / "ardoise_cwd.sh"
INLINE_CAP_BYTES = 1_500_000
TIMEOUT_TOOLS = 900
TIMEOUT_INLINE = 1200
MAX_TURNS_TOOLS = "50"


def load_answers(out: Path) -> dict[str, dict]:
    data = json.loads((out / "answers" / "answers.json").read_text())
    return {q["qid"]: q for q in data["questions"]}


def deposit_dir(out: Path, q: dict, fmt: str, fid_override: str | None = None) -> Path:
    return out / "fixtures" / (fid_override or q["fid"]) / fmt / ".mise" / q["deposit"]


def hash_deposit(dep: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(dep.rglob("*")):
        if f.is_file():
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def build_prompt(q: dict, fmt: str, arm: str, dep: Path) -> str:
    kind = "document" if q["deposit"].startswith("doc--") else "spreadsheet"
    if arm == "tools":
        return (f"In the folder ./.mise/{q['deposit']}/ is a {kind} fetched from "
                f"Google Drive. {q['question']}")
    parts = [f"Below are the files of a {kind} fetched from Google Drive "
             f"(deposit folder {q['deposit']})."]
    for f in sorted(dep.iterdir(), key=lambda p: (p.name != "manifest.json", p.name)):
        parts.append(f"\n--- {f.name} ---\n{f.read_text()}")
    parts.append(f"\n{q['question']}")
    # Without this line a tool-native subject burns its single no-tools turn
    # ANNOUNCING a script ("I'll compute this with a quick script…") — 9 of 11
    # inline misses on 2026-08-17 were plans, not answers. A bare API reader
    # (Flash) is unaffected: it has no tool concept either way.
    parts.append("\nYou have no tools in this environment — answer directly "
                 "from the content shown above, showing any working in text.")
    prompt = "\n".join(parts)
    if len(prompt.encode()) > INLINE_CAP_BYTES:
        raise ValueError(f"inline prompt {len(prompt.encode())} bytes exceeds cap — "
                         f"this scale is tools-arm only")
    return prompt


def one_run(run: dict, out: Path, answers: dict) -> str:
    rid = run["run_id"]
    tdir = out / "transcripts"
    tdir.mkdir(exist_ok=True)
    tpath = tdir / f"{rid}.jsonl"
    if tpath.exists() and '"type":"result"' in tpath.read_text(errors="replace"):
        return f"SKIP {rid} (result present)"

    q = answers[run["qid"]]
    fmt = run["format"]
    src = deposit_dir(out, q, fmt, run.get("fid_override"))
    world = out / "runs" / rid
    if world.exists():
        shutil.rmtree(world)
    (world / ".mise").mkdir(parents=True)
    dst = world / ".mise" / q["deposit"]
    shutil.copytree(src, dst)
    before = hash_deposit(dst)

    prompt = build_prompt(q, fmt, run["arm"], dst)
    cmd = ["timeout", str(TIMEOUT_INLINE if run["arm"] == "inline" else TIMEOUT_TOOLS),
           str(ARDOISE), "-p", "--cwd", str(world), "--dangerously-skip-permissions",
           "--output-format", "stream-json", "--verbose"]
    if run["model"] == "opus":
        cmd += ["--model", "opus"]
    elif run["model"] != "fable":
        cmd += ["--model", run["model"]]
    if run["arm"] == "inline":
        cmd += ["--tools", "", "--max-turns", "1", "--stdin"]
    else:
        cmd += ["--max-turns", MAX_TURNS_TOOLS]

    t0 = time.time()
    with tpath.open("w") as fh, (tdir / f"{rid}.err").open("w") as eh:
        if run["arm"] == "inline":
            proc = subprocess.run(cmd, input=prompt, text=True, stdout=fh, stderr=eh)
        else:
            proc = subprocess.run(cmd + ["--", prompt], text=True, stdout=fh, stderr=eh)
    dur = int(time.time() - t0)
    mutated = hash_deposit(dst) != before
    strays = [p.name for p in world.rglob("*")
              if p.is_file() and not str(p).startswith(str(dst))]
    return (f"DONE {rid} rc={proc.returncode} dur={dur}s "
            f"mutated={'YES' if mutated else 'no'} strays={strays or 'none'}")


GATE_FORMATS = ["aligned", "md-min", "json-min"]


def make_pilot_plan(out: Path, answers: dict) -> Path:
    """The discrimination gate (design doc): tools-arm-heavy after the smoke's
    cost finding — an inline 2k-row run costs 3-4x an agentic one (fresh cache
    write vs discounted cache reads), so the gate proves spread on the cheap
    arm and keeps inline to a 3-run plumbing check; inline spread is league work.

    Arms: tools x {2000, 10000} x 10 questions x 3 formats           = 60
          sabotage (scrambled headers) tools x 6 questions x 3 fmts  = 18
          tripled instance (path variance) tools x 3 fmts x 2 extra  =  6
          inline plumbing check x 3 formats                          =  3
    """
    plan = []
    fams = ["lookup-0", "lookup-1", "lookup-2", "aggregate-0", "aggregate-1",
            "aggregate-2", "rank-0", "quote-0", "quote-1", "quote-2"]
    for scale in (2000, 10000):
        for suffix in fams:
            for fmt in GATE_FORMATS:
                plan.append({"run_id": f"p-t-{scale}-{fmt}-{suffix}",
                             "qid": f"s1-{scale}-{suffix}", "format": fmt,
                             "arm": "tools", "model": "fable"})
    for suffix in ["lookup-0", "lookup-1", "lookup-2",
                   "aggregate-0", "aggregate-1", "aggregate-2"]:
        for fmt in GATE_FORMATS:
            plan.append({"run_id": f"p-sab-{fmt}-{suffix}",
                         "qid": f"s1-2000-{suffix}", "format": fmt, "arm": "tools",
                         "model": "fable", "fid_override": "s1-2000-sabotaged"})
    for fmt in GATE_FORMATS:
        for rep in (2, 3):
            plan.append({"run_id": f"p-t-2000-{fmt}-lookup-1-rep{rep}",
                         "qid": "s1-2000-lookup-1", "format": fmt,
                         "arm": "tools", "model": "fable"})
    for fmt in GATE_FORMATS:
        plan.append({"run_id": f"p-i-2000-{fmt}-lookup-0",
                     "qid": "s1-2000-lookup-0", "format": fmt,
                     "arm": "inline", "model": "fable"})
    p = out / "pilot.json"
    p.write_text(json.dumps(plan, indent=1))
    return p


ALL_FORMATS = ["aligned", "aligned-hr", "csv", "dual", "json-min", "md-min", "tsv"]
S1_QIDS = [f"{fam}-{i}" for fam in ("lookup", "aggregate", "quote") for i in range(3)] + ["rank-0"]
HARD_QIDS = {  # the never-gated slices; 3 distinct instances per cell
    "s2-wide": [f"widecross-{i}" for i in range(3)] + [f"quote-{i}" for i in range(3)],
    "s4-buried": [f"locate-{i}" for i in range(3)] + [f"lookup-{i}" for i in range(3)],
    "s5-join": [f"joingap-{i}" for i in range(3)] + ["joinset-0"],
}
SAB_QIDS = [f"{fam}-{i}" for fam in ("lookup", "aggregate") for i in range(3)]


def make_league_plans(out: Path, answers: dict) -> list[Path]:
    """The league (design doc §Gate run results → league consequences).

    flash-league: the accuracy canary — all 7 formats, every slice the
      1.5MB inline cap admits (S1 ≤2k; 10k/50k are tools-arm-only), plus
      sabotage. Foregone cells for strong readers are NOT foregone here.
    claude-league: only cells where something is still open —
      tooled S2/S4/S5 (cost + method recruitment; dual joins the gate trio
      because whether the sidecar CSV gets USED is a method question),
      tooled S1-50k (the router-economics tier, instance-0 only),
      inline sabotage (detect-recover WITHOUT tools — the gate only
      proved it tooled), and inline harder slices at instance-0.
      Strong-reader tooled S1 ≤10k accuracy is measured at ceiling: dropped.
    """
    flash, claude = [], []
    for scale in (200, 500, 2000):
        for suffix in S1_QIDS:
            for fmt in ALL_FORMATS:
                flash.append({"run_id": f"L-f-{scale}-{fmt}-{suffix}",
                              "qid": f"s1-{scale}-{suffix}", "format": fmt,
                              "arm": "inline", "model": "flash"})
    for suffix in SAB_QIDS:
        for fmt in ALL_FORMATS:
            flash.append({"run_id": f"L-f-sab-{fmt}-{suffix}",
                          "qid": f"s1-2000-{suffix}", "format": fmt,
                          "arm": "inline", "model": "flash",
                          "fid_override": "s1-2000-sabotaged"})
    for fid, suffixes in HARD_QIDS.items():
        for suffix in suffixes:
            for fmt in ALL_FORMATS:
                flash.append({"run_id": f"L-f-{fid}-{fmt}-{suffix}",
                              "qid": f"{fid}-{suffix}", "format": fmt,
                              "arm": "inline", "model": "flash"})

    tooled_fmts = ["aligned", "md-min", "json-min", "dual"]
    for model in ("fable", "opus"):
        m = model[0]
        for fid, suffixes in HARD_QIDS.items():
            for suffix in suffixes:
                for fmt in tooled_fmts:
                    claude.append({"run_id": f"L-{m}-t-{fid}-{fmt}-{suffix}",
                                   "qid": f"{fid}-{suffix}", "format": fmt,
                                   "arm": "tools", "model": model})
        for suffix in ("lookup-0", "aggregate-0", "rank-0"):
            for fmt in ("aligned", "md-min", "json-min"):
                claude.append({"run_id": f"L-{m}-t-50000-{fmt}-{suffix}",
                               "qid": f"s1-50000-{suffix}", "format": fmt,
                               "arm": "tools", "model": model})
        for suffix in ("lookup-0", "aggregate-0"):
            for fmt in ("aligned", "json-min"):
                claude.append({"run_id": f"L-{m}-i-sab-{fmt}-{suffix}",
                               "qid": f"s1-2000-{suffix}", "format": fmt,
                               "arm": "inline", "model": model,
                               "fid_override": "s1-2000-sabotaged"})
        for fid in HARD_QIDS:
            for suffix in (HARD_QIDS[fid][0], HARD_QIDS[fid][3]):
                for fmt in ("aligned", "md-min", "json-min"):
                    claude.append({"run_id": f"L-{m}-i-{fid}-{fmt}-{suffix}",
                                   "qid": f"{fid}-{suffix}", "format": fmt,
                                   "arm": "inline", "model": model})
    paths = []
    for name, plan in (("flash-league.json", flash), ("claude-league.json", claude)):
        p = out / name
        p.write_text(json.dumps(plan, indent=1))
        paths.append(p)
        print(f"{name}: {len(plan)} runs")
    return paths


def make_smoke_plan(out: Path, answers: dict) -> Path:
    """Four runs proving the harness end-to-end: both arms x two formats,
    on the cheap 2000-row tier, session-default model."""
    plan = [
        {"run_id": "smoke-t-aligned", "qid": "s1-2000-lookup-0", "format": "aligned",
         "arm": "tools", "model": "fable"},
        {"run_id": "smoke-t-jsonmin", "qid": "s1-2000-aggregate-0", "format": "json-min",
         "arm": "tools", "model": "fable"},
        {"run_id": "smoke-i-aligned", "qid": "s1-2000-lookup-0", "format": "aligned",
         "arm": "inline", "model": "fable"},
        {"run_id": "smoke-i-quote", "qid": "s1-2000-quote-0", "format": "csv",
         "arm": "inline", "model": "fable"},
    ]
    p = out / "smoke.json"
    p.write_text(json.dumps(plan, indent=1))
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "bench-work"))
    ap.add_argument("--plan")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--make-smoke-plan", action="store_true")
    ap.add_argument("--make-pilot-plan", action="store_true")
    ap.add_argument("--make-league-plans", action="store_true")
    a = ap.parse_args()
    out = Path(a.out).expanduser()
    answers = load_answers(out)

    if a.make_smoke_plan:
        print(f"wrote {make_smoke_plan(out, answers)}")
        return
    if a.make_pilot_plan:
        print(f"wrote {make_pilot_plan(out, answers)}")
        return
    if a.make_league_plans:
        for p in make_league_plans(out, answers):
            plan = json.loads(p.read_text())
            missing = [r["qid"] for r in plan if r["qid"] not in answers]
            assert not missing, f"{p.name}: qids not in answers.json: {missing[:5]}"
            print(f"validated {p}")
        return

    plan = json.loads(Path(a.plan).expanduser().read_text())
    log = (out / "harness.log").open("a")
    print(f"{len(plan)} runs, {a.jobs} parallel; transcripts -> {out}/transcripts/")
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        for line in ex.map(lambda r: one_run(r, out, answers), plan):
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            print(line)
            log.write(f"{stamp} {line}\n")
            log.flush()


if __name__ == "__main__":
    main()
