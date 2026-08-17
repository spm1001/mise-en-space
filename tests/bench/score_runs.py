# /// script
# requires-python = ">=3.11"
# ///
"""Mechanical scorer: stream-json transcripts -> results.csv.

Method taxonomy (final label = highest-priority behaviour observed):
    read-whole    entire big content file into context (Read covering >80% of
                  lines, or cat/less of it) — trumps everything: context was paid
    queried       DuckDB / Polars / SQL executed
    programmatic  python-csv/pandas/awk computation (tool-mediated, not the
                  named engines) — the wave's `programmatic-other`
    grep-targeted grep/rg/sed -n against the deposit
    head-sampled  partial reads only
    guessed       no deposit-touching tool use at all

Correctness is exact-value: Decimal equality for numerics (grouped digits,
brackets-negative, n/m understood), verbatim containment for quotes (scored
byte-exact AND typography-normalised — the census drift dimension), exact set
match for the join-set family. Every extraction failure is a flag, never a
silent skip.

    uv run --script score_runs.py --plan smoke.json          # score those runs
    uv run --script score_runs.py --self-test                # known-good/bad pair
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from generator import REGIONS

MONEY = re.compile(r"\(?-?£?\$?\d[\d,]*(?:\.\d+)?\)?")
QUERY_PAT = re.compile(r"duckdb|polars|sqlite3?|\bjq\b|\bsql\b", re.I)
PROG_PAT = re.compile(r"python3?\b|\bawk\b|pandas", re.I)
GREP_PAT = re.compile(r"\bgrep\b|\brg\b|\bsed\s+-n|\bawk\s+'?/", re.I)
SAMPLE_PAT = re.compile(r"\bhead\b|\btail\b", re.I)
# whole-read only counts against CONTENT files — `cat manifest.json` is triage,
# not a paid read of the table (mislabelled a jq-querying smoke run, 2026-08-17)
WHOLE_PAT = re.compile(r"\b(?:cat|less|more)\b[^|;&>]*content[_.]", re.I)


def norm_typo(s: str) -> str:
    for a, b in [("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'),
                 (" ", " "), ("–", "-"), ("—", "-"), ("−", "-")]:
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def to_decimal(tok: str) -> Decimal | None:
    neg = tok.startswith("(") and tok.endswith(")")
    t = tok.strip("()").lstrip("£$").replace(",", "")
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    return -d if neg and d > 0 else d


def extract_decimals(text: str) -> set[Decimal]:
    out = set()
    for m in MONEY.finditer(text):
        d = to_decimal(m.group())
        if d is not None:
            out.add(d)
    return out


def parse_transcript(path: Path):
    model, tools, result = None, [], None
    for line in path.read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            model = ev.get("model")
        elif ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tools.append((block.get("name"), block.get("input") or {}))
        elif ev.get("type") == "result":
            result = ev
    return model, tools, result


def classify(tools: list, content_lines: int | None) -> tuple[str, dict]:
    saw = {"read_whole": False, "queried": False, "programmatic": False,
           "grep": False, "sampled": False, "touched": False}
    covered = 0
    for name, inp in tools:
        if name == "Read" and "/.mise/" in str(inp.get("file_path", "")):
            saw["touched"] = True
            limit = inp.get("limit") or 2000
            covered += int(limit)
        elif name in ("Grep", "Glob"):
            saw["grep"] = saw["touched"] = True
        elif name == "Bash":
            cmd = str(inp.get("command", ""))
            if ".mise" not in cmd and "content" not in cmd:
                continue
            saw["touched"] = True
            if QUERY_PAT.search(cmd):
                saw["queried"] = True
            elif PROG_PAT.search(cmd):
                saw["programmatic"] = True
            elif GREP_PAT.search(cmd):
                saw["grep"] = True
            elif WHOLE_PAT.search(cmd):
                saw["read_whole"] = True
            elif SAMPLE_PAT.search(cmd):
                saw["sampled"] = True
    if content_lines and covered >= 0.8 * content_lines:
        saw["read_whole"] = True
    elif covered:
        saw["sampled"] = True
    order = [("read_whole", "read-whole"), ("queried", "queried"),
             ("programmatic", "programmatic"), ("grep", "grep-targeted"),
             ("sampled", "head-sampled")]
    label = next((lab for k, lab in order if saw[k]), "guessed")
    return label, saw


def score_one(run: dict, q: dict, out: Path) -> dict:
    rid = run["run_id"]
    tpath = out / "transcripts" / f"{rid}.jsonl"
    row = {"run_id": rid, "qid": q["qid"], "fid": q["fid"], "family": q["family"],
           "format": run["format"], "arm": run["arm"], "model_req": run["model"],
           "model": "", "method": "", "correct": "", "display_preserved": "",
           "naive_hit": "", "quote_exact": "", "quote_norm": "", "tool_calls": 0,
           "tokens_in": "", "tokens_out": "", "cache_read": "", "cache_write": "",
           "cost_usd": "", "turns": "", "duration_s": "", "flags": ""}
    flags = []
    if run.get("fid_override"):
        flags.append(f"fixture:{run['fid_override']}")
    if not tpath.exists():
        row["flags"] = "NO_TRANSCRIPT"
        return row
    model, tools, result = parse_transcript(tpath)
    row["model"] = model or ""
    row["tool_calls"] = len(tools)
    if result is None:
        row["flags"] = "NO_RESULT_EVENT"
        return row
    text = result.get("result") or ""
    usage = result.get("usage") or {}
    row.update(tokens_in=usage.get("input_tokens", ""),
               tokens_out=usage.get("output_tokens", ""),
               cache_read=usage.get("cache_read_input_tokens", ""),
               cache_write=usage.get("cache_creation_input_tokens", ""),
               cost_usd=result.get("total_cost_usd", ""),
               turns=result.get("num_turns", ""),
               duration_s=round((result.get("duration_ms") or 0) / 1000))

    # method (inline arm is definitionally no-tools)
    if run["arm"] == "inline":
        row["method"] = "inline-read"
    else:
        content_lines = None
        dep = (out / "fixtures" / (run.get("fid_override") or q["fid"])
               / run["format"] / ".mise" / q["deposit"])
        mains = sorted(dep.glob("content_*")) or sorted(dep.glob("content.*"))
        if mains:
            content_lines = mains[0].read_text(errors="replace").count("\n")
        row["method"], _ = classify(tools, content_lines)

    # correctness by family
    fam = q["family"]
    got = extract_decimals(text)
    if fam in ("lookup", "widecross"):
        expected = Decimal(q["answer_decimal"])
        row["correct"] = int(expected in got or abs(expected) in got and expected < 0)
        row["display_preserved"] = int(q.get("answer_display", "\x00") in text)
    elif fam == "aggregate":
        # naive_hit is a MENTION flag: subjects that get it right often narrate
        # the bracket judgement and quote both totals. Bracket-mishandling in
        # findings = (correct==0 AND naive_hit==1), never naive_hit alone.
        row["correct"] = int(Decimal(q["answer_decimal"]) in got)
        row["naive_hit"] = int(Decimal(q["naive_decimal"]) in got)
    elif fam == "joingap":
        expected = Decimal(q["answer_decimal"])
        if expected in got:
            row["correct"] = 1
        elif abs(expected) in got:
            row["correct"] = int(bool(re.search(
                r"short|below|under|miss|deficit" if expected < 0 else
                r"exceed|above|over|ahead", text, re.I)))
            flags.append("signed_via_direction_word")
        else:
            row["correct"] = 0
    elif fam in ("rank", "locate"):
        ans = q["answer_string"].lower()
        ok = ans in text.lower()
        if not ok and "accept_number" in q:
            head = ans.split(". ", 1)[1]
            # "Section number:** 11" is a correct locate — Flash's bullet style
            # broke the bare "section 11" fallback (6 false misses, 2026-08-17)
            ok = (re.search(rf"section(?:\s+number)?\s*[:*]*\s*{q['accept_number']}\b",
                            text, re.I) is not None
                  and head.lower() in text.lower())
        row["correct"] = int(ok)
    elif fam == "joinset":
        predicted = {r for r in REGIONS if r.lower() in text.lower()}
        row["correct"] = int(predicted == set(q["answer_set"]))
        if not row["correct"]:
            # A workings table names EVERY region ("London ... No"), so the
            # mention-set sweeps in non-answers. Second pass: a region counts
            # only if one of its lines carries an affirmative marker.
            claimed = set()
            for line in text.splitlines():
                low = line.lower()
                for reg in REGIONS:
                    if reg.lower() in low and (
                            re.search(r"\byes\b", low)
                            or ("exceed" in low and not re.search(
                                r"\bnot\b|\bno\b|did not|below|under", low))):
                        claimed.add(reg)
            if claimed == set(q["answer_set"]):
                row["correct"] = 1
                flags.append("joinset_line_extract")
            else:
                flags.append("set_mismatch_hand_review")
    elif fam == "quote":
        expected = q["expected_line_by_format"].get(run["format"])
        if expected is None:
            row["quote_exact"] = row["quote_norm"] = "NA"
            flags.append("no_expected_line_for_format")
        else:
            row["quote_exact"] = int(expected in text)
            row["quote_norm"] = int(norm_typo(expected) in norm_typo(text))
        row["correct"] = row["quote_exact"] if row["quote_exact"] != "NA" else ""
    row["flags"] = ";".join(flags)
    return row


FIELDS = ["run_id", "qid", "fid", "family", "format", "arm", "model_req", "model",
          "method", "correct", "display_preserved", "naive_hit", "quote_exact",
          "quote_norm", "tool_calls", "tokens_in", "tokens_out", "cache_read",
          "cache_write", "cost_usd", "turns", "duration_s", "flags"]


def self_test(out: Path) -> int:
    """Known-good and known-bad synthetic transcripts through the real path."""
    import tempfile
    answers = {"qid": "st-aggregate-0", "fid": "st", "family": "aggregate",
               "deposit": "sheet--x--000000000000",
               "answer_decimal": "1234.56", "naive_decimal": "2234.56"}
    good = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "model": "claude-test"}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "duckdb -c 'select 1' .mise/x/content_campaigns.csv"}}]}}),
        json.dumps({"type": "result", "result": "Total spend: 1,234.56", "usage":
                    {"input_tokens": 10, "output_tokens": 5}, "num_turns": 2}),
    ])
    bad = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "model": "claude-test"}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "cat .mise/x/content_campaigns.csv"}}]}}),
        json.dumps({"type": "result", "result": "The total is 2,234.56", "usage":
                    {"input_tokens": 10, "output_tokens": 5}, "num_turns": 2}),
    ])
    with tempfile.TemporaryDirectory() as td:
        w = Path(td)
        (w / "transcripts").mkdir()
        (w / "transcripts" / "st-good.jsonl").write_text(good)
        (w / "transcripts" / "st-bad.jsonl").write_text(bad)
        g = score_one({"run_id": "st-good", "format": "csv", "arm": "tools",
                       "model": "fable"}, answers, w)
        b = score_one({"run_id": "st-bad", "format": "csv", "arm": "tools",
                       "model": "fable"}, answers, w)
    checks = [
        ("good-method-queried", g["method"] == "queried"),
        ("good-correct", g["correct"] == 1),
        ("good-no-naive", g["naive_hit"] == 0),
        ("bad-method-read-whole", b["method"] == "read-whole"),
        ("bad-incorrect", b["correct"] == 0),
        ("bad-naive-hit", b["naive_hit"] == 1),
    ]
    for name, ok in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _, ok in checks) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "bench-work"))
    ap.add_argument("--plan")
    ap.add_argument("--results", default="results.csv")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    out = Path(a.out).expanduser()
    if a.self_test:
        sys.exit(self_test(out))

    answers = {q["qid"]: q for q in
               json.loads((out / "answers" / "answers.json").read_text())["questions"]}
    plan = json.loads(Path(a.plan).expanduser().read_text())
    rows = [score_one(run, answers[run["qid"]], out) for run in plan]
    rpath = out / a.results
    with rpath.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} runs scored -> {rpath}")
    flagged = [r for r in rows if r["flags"]]
    if flagged:
        print(f"{len(flagged)} flagged for attention: "
              + ", ".join(f"{r['run_id']}({r['flags']})" for r in flagged[:10]))


if __name__ == "__main__":
    main()
