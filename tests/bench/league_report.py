# /// script
# requires-python = ">=3.11"
# ///
"""League tables from scored results: the numbers step 4's policy cites.

Reads one or more results CSVs (score_runs.py output), applies the hand
adjudications file if present (mechanical scoring, hand-review on
disagreement rows — each entry records what was read and when), and prints:

  accuracy   by (model-arm, slice-group, format)
  tokens     mean input tokens by format per slice (within one model family
             ONLY — SentencePiece and BPE counts never cross-compare)
  cost       mean cost per run by format/arm (cost_usd where the harness
             recorded it; Flash runs are priced at the fetched card via
             --flash-card IN/OUT, e.g. --flash-card 0.75/3.75)
  method     tooled-arm recruitment by format, plus dual sidecar usage
             (which of content.txt / content_grid.csv the commands touched,
             read from transcripts)

    uv run --script league_report.py --results flash-league-results-v2.csv \
        [--results claude-league-results.csv] [--flash-card 0.75/3.75]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

FMTS = ["aligned", "aligned-hr", "csv", "dual", "json-min", "md-min", "tsv"]


def slice_of(r: dict) -> str:
    if "fixture:" in r["flags"]:
        return "SABOTAGE"
    return r["fid"]


def load(out: Path, paths: list[str]) -> list[dict]:
    rows = []
    for p in paths:
        rows += list(csv.DictReader(open(out / p)))
    adj_path = out / "adjudications.json"
    if adj_path.exists():
        adj = {k: v for k, v in json.loads(adj_path.read_text()).items()
               if not k.startswith("_")}
        n = 0
        for r in rows:
            if r["run_id"] in adj:
                r["correct"] = str(adj[r["run_id"]]["correct"])
                n += 1
        print(f"[{n} hand adjudications applied from {adj_path.name}]")
    return rows


def key_of(r: dict) -> str:
    m = r["model"] or r["model_req"]
    m = ("flash" if "gemini" in m else
         "opus" if "opus" in m else
         "fable" if ("fable" in m or m == "") else m)
    return f"{m}-{r['arm']}"


def acc_table(rows: list[dict]) -> None:
    print("\n== ACCURACY (correct/scored) ==")
    groups = sorted({key_of(r) for r in rows})
    for g in groups:
        sub = [r for r in rows if key_of(r) == g and r["correct"] in ("0", "1")]
        if not sub:
            continue
        slices = sorted({slice_of(r) for r in sub})
        print(f"\n-- {g} --\n{'':14s}" + "".join(f"{f:>11s}" for f in FMTS))
        for s in slices:
            line = f"{s:14s}"
            for f in FMTS:
                cell = [r for r in sub if slice_of(r) == s and r["format"] == f]
                c = sum(int(r["correct"]) for r in cell)
                line += f"{(f'{c}/{len(cell)}' if cell else '-'):>11s}"
            print(line)


def cost_table(rows: list[dict], flash_card: tuple[float, float] | None) -> None:
    print("\n== COST (mean $/run; flash priced at card, claude from harness) ==")
    for g in sorted({key_of(r) for r in rows}):
        sub = [r for r in rows if key_of(r) == g]
        for s in sorted({slice_of(r) for r in sub}):
            line = f"{g:14s} {s:12s}"
            any_cost = False
            for f in FMTS:
                cell = [r for r in sub if slice_of(r) == s and r["format"] == f]
                costs = []
                for r in cell:
                    if r["cost_usd"]:
                        costs.append(float(r["cost_usd"]))
                    elif flash_card and g.startswith("flash") and r["tokens_in"]:
                        i, o = flash_card
                        costs.append(int(r["tokens_in"]) / 1e6 * i
                                     + int(r["tokens_out"] or 0) / 1e6 * o)
                if costs:
                    any_cost = True
                    line += f"{sum(costs)/len(costs):>11.3f}"
                else:
                    line += f"{'-':>11s}"
            if any_cost:
                print(line)


SIDECAR_PAT = re.compile(r"content_grid\.csv")
ALIGNED_PAT = re.compile(r"content\.txt")


def dual_sidecar(rows: list[dict], out: Path) -> None:
    """For tooled dual runs: which form did the commands touch?"""
    duals = [r for r in rows if r["format"] == "dual" and r["arm"] == "tools"
             and r["correct"] in ("0", "1")]
    if not duals:
        return
    print("\n== DUAL SIDECAR USAGE (tooled runs; grid=CSV sidecar, txt=aligned) ==")
    counts = defaultdict(lambda: defaultdict(int))
    for r in duals:
        tpath = out / "transcripts" / f"{r['run_id']}.jsonl"
        if not tpath.exists():
            continue
        text = tpath.read_text(errors="replace")
        used = ("both" if SIDECAR_PAT.search(text) and ALIGNED_PAT.search(text)
                else "grid" if SIDECAR_PAT.search(text)
                else "txt" if ALIGNED_PAT.search(text) else "neither")
        counts[key_of(r)][used] += 1
    for g, c in sorted(counts.items()):
        print(f"  {g:14s} " + "  ".join(f"{k}={v}" for k, v in sorted(c.items())))


DISCLOSE_PAT = re.compile(
    r"scrambl|corrupt|mismatch|header.*(wrong|incorrect|not match|mislabel|"
    r"does not describe|do not describe)|labels?.*(wrong|shifted|rotated)|"
    r"column.*(mislabel|shifted|rotated|offset)|inconsistent", re.I)


def sabotage_table(rows: list[dict], out: Path) -> None:
    """Detect-recover-DISCLOSE is the trust property; regex sweep + the rule
    that wrong-and-silent rows get hand-read before the number ships."""
    sab = [r for r in rows if slice_of(r) == "SABOTAGE" and r["correct"] in ("0", "1")]
    if not sab:
        return
    print("\n== SABOTAGE: right/wrong x disclosed/silent ==")
    for g in sorted({key_of(r) for r in sab}):
        c = defaultdict(int)
        for r in [x for x in sab if key_of(x) == g]:
            tpath = out / "transcripts" / f"{r['run_id']}.jsonl"
            text = ""
            for line in tpath.read_text(errors="replace").splitlines():
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "result":
                    text = ev.get("result") or ""
            d = "disclosed" if DISCLOSE_PAT.search(text) else "SILENT"
            c[f"{'right' if r['correct'] == '1' else 'WRONG'}+{d}"] += 1
        print(f"  {g:14s} " + "  ".join(f"{k}={v}" for k, v in sorted(c.items())))


def method_table(rows: list[dict]) -> None:
    tooled = [r for r in rows if r["arm"] == "tools" and r["method"]]
    if not tooled:
        return
    print("\n== METHOD RECRUITMENT (tooled arms) ==")
    for g in sorted({key_of(r) for r in tooled}):
        sub = [r for r in tooled if key_of(r) == g]
        print(f"-- {g} --")
        for f in FMTS:
            cell = [r["method"] for r in sub if r["format"] == f]
            if not cell:
                continue
            c = defaultdict(int)
            for m in cell:
                c[m] += 1
            print(f"  {f:12s} " + "  ".join(f"{k}={v}" for k, v in sorted(c.items())))


def token_table(rows: list[dict]) -> None:
    print("\n== MEAN INPUT TOKENS by format (within one model family only) ==")
    for g in sorted({key_of(r) for r in rows}):
        sub = [r for r in rows if key_of(r) == g and r["tokens_in"]
               and r["arm"] == "inline"]
        for s in sorted({slice_of(r) for r in sub}):
            base_cell = [int(r["tokens_in"]) for r in sub
                         if slice_of(r) == s and r["format"] == "aligned"]
            if not base_cell:
                continue
            base = sum(base_cell) / len(base_cell)
            line = f"{g:14s} {s:12s}"
            for f in FMTS:
                cell = [int(r["tokens_in"]) for r in sub
                        if slice_of(r) == s and r["format"] == f]
                if cell:
                    m = sum(cell) / len(cell)
                    line += f"{(m/base-1)*100:>+10.0f}%"
                else:
                    line += f"{'-':>11s}"
            print(line + "   (vs aligned)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "bench-work"))
    ap.add_argument("--results", action="append", required=True)
    ap.add_argument("--flash-card", default=None,
                    help="IN/OUT $ per M tokens, e.g. 0.75/3.75")
    a = ap.parse_args()
    out = Path(a.out).expanduser()
    card = None
    if a.flash_card:
        i, o = a.flash_card.split("/")
        card = (float(i), float(o))
    rows = load(out, a.results)
    print(f"{len(rows)} scored rows from {len(a.results)} file(s)")
    acc_table(rows)
    token_table(rows)
    cost_table(rows, card)
    method_table(rows)
    dual_sidecar(rows, out)
    sabotage_table(rows, out)


if __name__ == "__main__":
    main()
