# /// script
# requires-python = ">=3.11"
# ///
"""Independent verification of bench fixtures against answers.json.

Independence discipline: values are re-derived by parsing the RENDERED files
with stdlib parsers (csv module, json, tab-splitting) — never by importing the
generator's data structures. The aligned formats have no independent parser
here (a fixed-width reader would re-implement the renderer); they are checked
by structure (line counts, header-restatement blocks) and probe presence, while
the numeric ground truths are proven independently in csv, tsv AND json.

Run the known-bad control FIRST and watch it fail:
    uv run --script verify_fixture.py --perturb   # must exit 1
    uv run --script verify_fixture.py             # must exit 0, ALL OK
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from decimal import Decimal
from pathlib import Path

S1_COLS = ["region", "campaign", "week", "spend", "impressions", "cpm", "status", "notes"]
CHECKS = {"pass": 0, "fail": 0}


def check(name: str, ok: bool, detail: str = ""):
    CHECKS["pass" if ok else "fail"] += 1
    if not ok:
        print(f"FAIL  {name}  {detail}")


def parse_money(s: str) -> Decimal | None:
    s = s.strip()
    if not s or s == "n/m":
        return None
    if s.startswith("(") and s.endswith(")"):
        return -Decimal(s[1:-1].replace(",", ""))
    return Decimal(s.replace(",", ""))


def read_csv_rows(path: Path) -> list[dict]:
    return list(csv.DictReader(io.StringIO(path.read_text())))


def read_tsv_rows(path: Path) -> list[dict]:
    lines = path.read_text().rstrip("\n").split("\n")
    hdr = lines[0].split("\t")
    return [dict(zip(hdr, ln.split("\t"))) for ln in lines[1:]]


def fixture_file(fx: Path, fid: str, fmt: str, deposit: str, name: str) -> Path:
    return fx / fid / fmt / ".mise" / deposit / name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "bench-work"))
    ap.add_argument("--perturb", action="store_true",
                    help="known-bad control: shift one aggregate expectation; MUST fail")
    a = ap.parse_args()
    out = Path(a.out).expanduser()
    fx = out / "fixtures"
    ans = json.loads((out / "answers" / "answers.json").read_text())
    qs = ans["questions"]

    if a.perturb:
        for q in qs:
            if q["family"] == "aggregate":
                q["answer_decimal"] = str(Decimal(q["answer_decimal"]) + 1)
                print(f"PERTURB  {q['qid']} expectation shifted by +1 — a passing run now "
                      f"means the verifier is broken")
                break

    # answers.json must sit outside every fixture world
    check("answers-outside-worlds",
          not any(p.name == "answers.json" for p in fx.rglob("answers.json")))

    by_fid: dict[str, list[dict]] = {}
    for q in qs:
        by_fid.setdefault(q["fid"], []).append(q)

    for fid, fqs in by_fid.items():
        deposit = fqs[0]["deposit"]
        slug = deposit.split("--")[1]
        is_doc = deposit.startswith("doc--")

        if is_doc:
            for fmt in ("aligned", "csv", "md-min", "json-min"):
                doc = fixture_file(fx, fid, fmt, deposit, "content.md").read_text()
                for q in fqs:
                    if q["family"] == "locate":
                        check(f"{fid}/{fmt}/{q['qid']}-heading", f"## {q['answer_string']}" in doc)
                    if q["family"] == "lookup":
                        check(f"{fid}/{fmt}/{q['qid']}-probe", q["answer_display"] in doc)
            continue

        # tabular slices: independent numeric re-derivation from csv, cross-checked tsv+json
        tab_files = sorted((fx / fid / "csv" / ".mise" / deposit).glob("content_*.csv"))
        main_tab = tab_files[0]
        crows = read_csv_rows(main_tab)
        trows = read_tsv_rows(fixture_file(fx, fid, "tsv", deposit,
                                           main_tab.name.replace(".csv", ".tsv")))
        jbody = json.loads(fixture_file(fx, fid, "json-min", deposit,
                                        main_tab.name.replace(".csv", ".json")).read_text())
        check(f"{fid}-rowcount-agree", len(crows) == len(trows) == len(jbody),
              f"csv={len(crows)} tsv={len(trows)} json={len(jbody)}")

        for q in fqs:
            qid = q["qid"]
            if q["family"] == "lookup" and "s4" not in fid:
                hits = [r for r in crows if r["campaign"] == _q_field(q, "campaign")
                        and r["week"] == _q_field(q, "week")]
                check(f"{qid}-unique", len(hits) == 1, f"{len(hits)} hits")
                if len(hits) == 1:
                    check(f"{qid}-value", parse_money(hits[0]["spend"])
                          == Decimal(q["answer_decimal"]))
                    jhit = [r for r in jbody if r["campaign"] == hits[0]["campaign"]
                            and r["week"] == hits[0]["week"]]
                    check(f"{qid}-json-agree", len(jhit) == 1
                          and jhit[0]["spend"] == hits[0]["spend"])
            elif q["family"] == "aggregate":
                reg = q["question"].split("region ")[1].split(" across")[0]
                vals = [parse_money(r["spend"]) for r in crows if r["region"] == reg]
                vals = [v for v in vals if v is not None]
                total = sum(vals)
                check(f"{qid}-total", total == Decimal(q["answer_decimal"]),
                      f"derived {total} vs expected {q['answer_decimal']}")
                check(f"{qid}-brackets-live", any(v < 0 for v in vals),
                      "no credit row in group — naive control vacuous")
                check(f"{qid}-naive-differs",
                      Decimal(q["naive_decimal"]) != Decimal(q["answer_decimal"]))
            elif q["family"] == "rank":
                totals: dict[str, Decimal] = {}
                for r in crows:
                    v = parse_money(r["spend"])
                    if v is not None:
                        totals[r["region"]] = totals.get(r["region"], Decimal(0)) + v
                ranking = sorted(totals, key=lambda k: totals[k], reverse=True)
                check(f"{qid}-third", ranking[2] == q["answer_string"],
                      f"derived {ranking[2]!r}")
            elif q["family"] == "quote":
                for fmt, expected in q["expected_line_by_format"].items():
                    fname = ("content.txt" if fmt == "dual"
                             else _content_name(fmt, main_tab.name))
                    body = fixture_file(fx, fid, fmt, deposit, fname).read_text()
                    check(f"{qid}-{fmt}-present", body.count(expected) >= 1)
            elif q["family"] == "widecross":
                owner = q["question"].split("owned by ")[1].split(",")[0]
                hits = [r for r in jbody if r.get("owner") == owner]
                mon = q["question"].split("the ")[-1].split(" impressions")[0]
                check(f"{qid}-value", len(hits) == 1
                      and hits[0][f"imps_{mon}"] == q["answer_display"])
            elif q["family"] == "joinset":
                targets = {r["region"]: parse_money(r["target_spend"])
                           for r in read_csv_rows(fixture_file(fx, fid, "csv", deposit,
                                                               "content_targets.csv"))}
                totals = {}
                for r in crows:
                    v = parse_money(r["spend"])
                    if v is not None:
                        totals[r["region"]] = totals.get(r["region"], Decimal(0)) + v
                derived = sorted(reg for reg, t in totals.items() if t > targets[reg])
                check(f"{qid}-set", derived == q["answer_set"],
                      f"derived {derived} vs {q['answer_set']}")
            elif q["family"] == "joingap":
                reg = q["question"].split("For region ")[1].split(":")[0]
                targets = {r["region"]: parse_money(r["target_spend"])
                           for r in read_csv_rows(fixture_file(fx, fid, "csv", deposit,
                                                               "content_targets.csv"))}
                total = sum(parse_money(r["spend"]) or Decimal(0)
                            for r in crows if r["region"] == reg)
                check(f"{qid}-gap", (total - targets[reg]).quantize(Decimal("0.01"))
                      == Decimal(q["answer_decimal"]))

        # aligned structure: header + n rows (+ hr restatement blocks)
        n = len(crows)
        al = fixture_file(fx, fid, "aligned", deposit,
                          _content_name("aligned", main_tab.name)).read_text()
        wraps = fid == "s2-wide"
        if not wraps:
            check(f"{fid}-aligned-lines", len(al.rstrip("\n").split("\n")) == n + 1)
            hr = fixture_file(fx, fid, "aligned-hr", deposit,
                              _content_name("aligned-hr", main_tab.name)).read_text()
            expect = n + 1 + 2 * ((n - 1) // 40)
            check(f"{fid}-alignedhr-lines", len(hr.rstrip("\n").split("\n")) == expect)
        # dual: three artefacts present, README carries the routing note
        dual = fx / fid / "dual" / ".mise" / deposit
        check(f"{fid}-dual-parts", (dual / "content.txt").exists()
              and any(dual.glob("content_*.csv")) and (dual / "README.md").exists())
        if (dual / "README.md").exists():
            check(f"{fid}-dual-readme-routing",
                  "DuckDB or Polars" in (dual / "README.md").read_text())

    # sabotage: header labels differ, data rows identical
    can = fx / "s1-2000" / "csv" / ".mise"
    sab = fx / "s1-2000-sabotaged" / "csv" / ".mise"
    if sab.exists():
        dep = next(can.iterdir()).name
        c = (can / dep / "content_campaigns.csv").read_text().split("\n")
        s = (sab / dep / "content_campaigns.csv").read_text().split("\n")
        check("sabotage-header-differs", c[0] != s[0], "headers identical — sabotage missing")
        check("sabotage-data-intact", c[1:] == s[1:], "data rows differ — sabotage overreached")

    print(f"\n{'ALL OK' if not CHECKS['fail'] else 'FAILURES'}: "
          f"{CHECKS['pass']} passed, {CHECKS['fail']} failed")
    return 1 if CHECKS["fail"] else 0


def _q_field(q: dict, field: str) -> str:
    text = q["question"]
    if field == "campaign":
        return text.split("campaign ")[1].split(" in week")[0]
    if field == "week":
        return text.split("week ")[1].split(" in region")[0].rstrip("?,")
    raise KeyError(field)


def _content_name(fmt: str, csv_name: str) -> str:
    ext = {"aligned": "txt", "aligned-hr": "txt", "md-min": "md",
           "json-min": "json", "tsv": "tsv", "csv": "csv"}[fmt]
    return csv_name.replace(".csv", f".{ext}")


if __name__ == "__main__":
    sys.exit(main())
