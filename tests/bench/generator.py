# /// script
# requires-python = ">=3.11"
# ///
"""Fixture generator for the deposit-format bench (mise-rolira, workstream C).

Builds the synthetic backbone: five census-weighted slices rendered into every
format arm, question instances with exact-value answers, and one sabotaged
variant (header row shuffled — the markitdown fingerprint) whose score
depression is the instrument-sensitivity control.

Everything is seeded and deterministic: same seed → byte-identical fixtures
(the runner sha256-checks against this property). Ground truths are computed
with Decimal from the same values the renderers receive, so generator and a
correct reader cannot drift apart on float rounding.

Usage:
    uv run --script generator.py [--out ~/bench-work] [--seed 84617]

Outputs (under --out):
    fixtures/<fid>/<format>/.mise/<deposit>/...      one world per slice x format
    answers/answers.json                             ground truths, OUTSIDE all worlds
"""

from __future__ import annotations

import argparse
import json
import random
from decimal import Decimal
from pathlib import Path

import render as R

SEED_DEFAULT = 84617
FETCHED_AT = "2026-08-17T12:00:00.000000+00:00"
CREATED = "2026-01-12T09:30:00.000Z"
MODIFIED = "2026-08-15T17:41:00.000Z"

REGIONS = [
    "London", "Midlands", "North West", "Yorkshire", "North East",
    "Central Scotland", "Wales & West", "South & South East", "East of England",
    "Northern Ireland", "Border", "South West",
]
BRAND_A = ["Harbour", "Northgate", "Fairlead", "Bexley", "Orchard", "Stanmore", "Caldwell",
           "Riverton", "Ashcombe", "Pennine", "Maple", "Foxton", "Granary", "Weldon",
           "Kestrel", "Lanyard", "Marlow", "Ottway", "Pimlico", "Quenby"]
BRAND_B = ["Foods", "Insurance", "Motors", "Energy", "Holidays", "Bakery", "Telecom",
           "Finance", "Home", "Health", "Outdoor", "Kitchens"]
QUALIFIERS = ["Q1 Push", "Spring Burst", "Brand Refresh", "Awareness H2", "Summer Flight",
              "Autumn Wave", "Launch Sprint", "Always-On", "Winter Peak", "Regional Test"]
STATUSES = ["Live", "Live", "Completed", "Completed", "Completed", "Paused", "Planned"]
NOTE_POOL = [
    "", "", "", "", "", "", "", "", "", "",
    "Reweighted w/c 12 Jan, per MB", "Copy rotation from wk 6",
    'Client flagged as "priority", hold CPM', "Late start, invoicing in arrears",
    "Regional uplift test, see tracker", "Makegood applied, credit follows",
]
OWNER_FIRST = ["Priya", "Callum", "Nadia", "Rhys", "Imogen", "Dougal", "Saskia", "Tomas",
               "Aoife", "Lennox", "Marta", "Ewan", "Zainab", "Piers", "Cerys", "Viktor"]
OWNER_LAST = ["Okafor", "Whitfield", "Brandt", "Llewellyn", "Sorensen", "MacRae", "Delgado",
              "Naismith", "Kowalska", "Trebilcock", "Osei", "Ferrant", "Hollis", "Braithwaite"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

S1_SCALES = [200, 500, 2000, 10000, 50000]
S1_COLS = ["region", "campaign", "week", "spend", "impressions", "cpm", "status", "notes"]
S1_NUMERIC = {"spend", "impressions", "cpm"}

DRIVE_ID_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"


def grp(d: Decimal) -> str:
    return f"{d:,.2f}"


def grpi(n: int) -> str:
    return f"{n:,}"


def drive_id(rng: random.Random) -> str:
    return "1" + "".join(rng.choice(DRIVE_ID_CHARS) for _ in range(43))


def slugify(title: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in title.lower()).strip("-")


# ── S1: the long campaign table ──────────────────────────────────────


def make_campaign_rows(rng: random.Random, n: int) -> list[dict]:
    """Rows carry display strings for rendering AND raw values for answers.
    (campaign, week) is globally unique: campaign names are unique and each
    campaign-week pair appears once. Credits (~0.5%) are round-robined across
    regions so every region aggregate exercises bracket handling at scale."""
    names = []
    for a in BRAND_A:
        for b in BRAND_B:
            for q in QUALIFIERS:
                names.append(f"{a} {b} {q}")
    rng.shuffle(names)

    rows, ni = [], 0
    while len(rows) < n:
        campaign = names[ni]
        region = REGIONS[ni % len(REGIONS)]
        ni += 1
        start = rng.randint(1, 20)
        for wk in range(start, min(start + rng.randint(8, 40), 53)):
            if len(rows) >= n:
                break
            spend = Decimal(rng.randint(80_000, 24_000_000)) / 100
            imps = rng.randint(40_000, 6_000_000)
            cpm = (spend / imps * 1000).quantize(Decimal("0.01"))
            rows.append({
                "region": region, "campaign": campaign, "week": f"2026-W{wk:02d}",
                "spend_val": spend, "credit": False,
                "display": {
                    "region": region, "campaign": campaign, "week": f"2026-W{wk:02d}",
                    "spend": grp(spend), "impressions": grpi(imps), "cpm": grp(cpm),
                    "status": rng.choice(STATUSES), "notes": rng.choice(NOTE_POOL),
                },
            })
    # credits: ~0.5%, min 6, round-robin regions so aggregates all see one
    n_credit = max(6, n // 200)
    idxs = rng.sample(range(n), n_credit)
    for k, i in enumerate(idxs):
        r = rows[i]
        amount = Decimal(rng.randint(30_000, 800_000)) / 100
        r["spend_val"] = -amount
        r["credit"] = True
        r["display"].update({
            "spend": f"({grp(amount)})", "impressions": "0", "cpm": "n/m",
            "notes": "Makegood applied, credit follows",
            "region": REGIONS[k % len(REGIONS)],
        })
        r["region"] = r["display"]["region"]
    return rows


# ── questions ────────────────────────────────────────────────────────


def find_quote_line(rendered: str, row: dict) -> str:
    """Locate the unique rendered line carrying this row (line-per-record formats)."""
    camp, week = row["display"]["campaign"], row["display"]["week"]
    hits = [ln for ln in rendered.split("\n") if camp in ln and week in ln]
    assert len(hits) == 1, f"quote target not unique: {camp} {week} -> {len(hits)} lines"
    return hits[0]


def s1_questions(fid: str, deposit: str, rows: list[dict], rng: random.Random,
                 rendered_by_fmt: dict[str, str]) -> list[dict]:
    n = len(rows)
    qs = []

    # T-lookup-deep: rows at 75-90% depth
    deep = rng.sample(range(int(n * 0.75), int(n * 0.9)), 3)
    for i, ri in enumerate(deep):
        r = rows[ri]
        d = r["display"]
        qs.append({
            "qid": f"{fid}-lookup-{i}", "fid": fid, "family": "lookup", "deposit": deposit,
            "question": (f"What was the spend for campaign {d['campaign']} in week {d['week']} "
                         f"in region {d['region']}? Give the exact figure."),
            "answer_decimal": str(r["spend_val"]), "answer_display": d["spend"],
            "row_1based": ri + 1,
        })

    # T-aggregate: regions guaranteed a credit row (round-robin construction)
    agg_regions = rng.sample(REGIONS[:6], 3)
    for i, reg in enumerate(agg_regions):
        vals = [r["spend_val"] for r in rows if r["region"] == reg]
        naive = sum(abs(v) for v in vals)
        qs.append({
            "qid": f"{fid}-aggregate-{i}", "fid": fid, "family": "aggregate", "deposit": deposit,
            "question": (f"What is the total spend for region {reg} across all campaigns "
                         f"and weeks? Exact figure."),
            "answer_decimal": str(sum(vals)), "naive_decimal": str(naive),
            "n_rows_in_group": len(vals),
        })

    # T-rank: 3rd-largest region by total spend
    totals = {reg: sum(r["spend_val"] for r in rows if r["region"] == reg) for reg in REGIONS}
    ranking = sorted(totals, key=lambda k: totals[k], reverse=True)
    margin = (totals[ranking[2]] - totals[ranking[3]]) / max(totals[ranking[2]], Decimal(1))
    qs.append({
        "qid": f"{fid}-rank-0", "fid": fid, "family": "rank", "deposit": deposit,
        "question": ("Which region has the third-largest total spend across all campaigns "
                     "and weeks? Name the region."),
        "answer_string": ranking[2], "ranking": ranking[:5], "margin_over_4th": str(margin),
    })

    # T-quote-out: noted, comma-bearing rows past half depth
    cands = [i for i, r in enumerate(rows)
             if i > n // 2 and "," in r["display"]["notes"]]
    for i, ri in enumerate(rng.sample(cands, min(3, len(cands)))):
        r = rows[ri]
        expected = {}
        for fmt, rendered in rendered_by_fmt.items():
            if fmt == "json-min":
                expected[fmt] = json.dumps({c: r["display"][c] for c in S1_COLS},
                                           separators=(",", ":"), ensure_ascii=False)
            else:
                expected[fmt] = find_quote_line(rendered, r)
        qs.append({
            "qid": f"{fid}-quote-{i}", "fid": fid, "family": "quote", "deposit": deposit,
            "question": (f"Find the row for campaign {r['display']['campaign']}, week "
                         f"{r['display']['week']}, and quote it exactly as it appears in the "
                         f"file so I can Ctrl-F it in the original sheet."),
            "expected_line_by_format": expected, "row_1based": ri + 1,
        })
    return qs


# ── deposit writing ──────────────────────────────────────────────────


def write_manifest(dep_dir: Path, title: str, fid_drive: str, tabs: list[dict],
                   rng: random.Random, doc: bool = False, extra: dict | None = None):
    m = {
        "type": "doc" if doc else "sheet", "title": title, "id": fid_drive,
        "fetched_at": FETCHED_AT,
    }
    if not doc:
        m["sheet_count"] = len(tabs)
        m["tabs"] = tabs
        m["formula_count"] = rng.randint(0, 30)
    m["warnings"] = []
    m["created_time"] = CREATED
    m["modified_time"] = MODIFIED
    if extra:
        m.update(extra)
    (dep_dir / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")


def sheet_header(tab: str) -> str:
    return f"=== Sheet: {tab} ===\n"


def write_sheet_deposit(root: Path, fmt: str, deposit: str, title: str, drive: str,
                        tabs: list[tuple[str, list[str], list[dict], set[str]]],
                        rng: random.Random, wrap: dict[str, int] | None = None) -> dict[str, str]:
    """tabs: [(tab_name, cols, rows, numeric_cols)]. Returns rendered text per tab."""
    dep = root / fmt / ".mise" / deposit
    dep.mkdir(parents=True, exist_ok=True)
    rendered = {}
    if fmt == "dual":
        # aligned for reading + CSV for computing + eye-level README (spec-2 verdict)
        combined_txt, combined_csv, tabmeta = [], [], []
        for i, (tab, cols, rows, num) in enumerate(tabs):
            disp = [r["display"] if "display" in r else r for r in rows]
            txt = R.render("aligned", cols, disp, num, wrap)
            csv_ = R.render("csv", cols, disp, num)
            rendered[tab] = txt
            combined_txt.append(sheet_header(tab) + txt)
            combined_csv.append(sheet_header(tab) + csv_)
            fn = f"content_{slugify(tab)}.csv"
            (dep / fn).write_text(csv_)
            tabmeta.append({"name": tab, "sheet_id": 0 if i == 0 else rng.randint(10**8, 2 * 10**9),
                            "filename": fn})
        (dep / "content.txt").write_text("\n".join(combined_txt))
        csv_name = tabmeta[0]["filename"] if len(tabmeta) == 1 else "content_<tab>.csv"
        (dep / "README.md").write_text(R.DUAL_README.format(csv_name=csv_name))
        write_manifest(dep, title, drive, tabmeta, rng)
        return rendered

    ext = R.EXT[fmt]
    tabmeta, combined = [], []
    for i, (tab, cols, rows, num) in enumerate(tabs):
        disp = [r["display"] if "display" in r else r for r in rows]
        body = R.render(fmt, cols, disp, num, wrap)
        rendered[tab] = body
        fn = f"content_{slugify(tab)}.{ext}"
        (dep / fn).write_text(body)
        combined.append((tab, body))
        tabmeta.append({"name": tab, "sheet_id": 0 if i == 0 else rng.randint(10**8, 2 * 10**9),
                        "filename": fn})
    if fmt == "json-min":
        obj = {tab: json.loads(body) for tab, body in combined}
        (dep / f"content.{ext}").write_text(json.dumps(obj, separators=(",", ":"),
                                                       ensure_ascii=False) + "\n")
    else:
        (dep / f"content.{ext}").write_text("\n".join(sheet_header(t) + b for t, b in combined))
    write_manifest(dep, title, drive, tabmeta, rng)
    return rendered


# ── S2: wide table ───────────────────────────────────────────────────


def make_wide(rng: random.Random):
    cols = (["region", "campaign", "owner", "status"]
            + [f"spend_{m}" for m in MONTHS] + [f"imps_{m}" for m in MONTHS]
            + ["fy_target", "variance_note", "notes"])
    numeric = {c for c in cols if c.startswith(("spend_", "imps_"))} | {"fy_target"}
    owners = [f"{a} {b}" for a in OWNER_FIRST for b in OWNER_LAST]
    rng.shuffle(owners)
    names = [f"{a} {b} {q}" for a, b, q in
             zip(rng.sample(BRAND_A * 3, 60), rng.sample(BRAND_B * 5, 60),
                 rng.sample(QUALIFIERS * 6, 60))]
    rows = []
    for i in range(60):
        vals = {f"spend_{m}": Decimal(rng.randint(0, 4_000_000)) / 100 for m in MONTHS}
        imps = {f"imps_{m}": rng.randint(0, 900_000) for m in MONTHS}
        note = rng.choice([
            "", "", "",
            "Phasing moved twice,\nsee planner log", "Target restated in May,\nboard pack v3",
            "Includes sponsorship\ncarry-over from 2025",
        ])
        disp = {"region": rng.choice(REGIONS), "campaign": names[i], "owner": owners[i],
                "status": rng.choice(STATUSES),
                **{k: grp(v) for k, v in vals.items()},
                **{k: grpi(v) for k, v in imps.items()},
                "fy_target": grp(sum(vals.values()) * Decimal(rng.randint(90, 115)) / 100),
                "variance_note": note, "notes": rng.choice(NOTE_POOL)}
        rows.append({"display": disp, "vals": vals, "imps": imps})
    return cols, numeric, rows


def s2_questions(fid: str, deposit: str, cols, rows, rng, rendered_by_fmt) -> list[dict]:
    qs = []
    picks = rng.sample(range(len(rows)), 3)
    for i, ri in enumerate(picks):
        r = rows[ri]
        mon = rng.choice(MONTHS)
        qs.append({
            "qid": f"{fid}-widecross-{i}", "fid": fid, "family": "widecross", "deposit": deposit,
            "question": (f"For the campaign owned by {r['display']['owner']}, what is the "
                         f"{mon} impressions figure? Exact figure."),
            "answer_decimal": str(r["imps"][f"imps_{mon}"]),
            "answer_display": r["display"][f"imps_{mon}"],
        })
    plain = [i for i, r in enumerate(rows) if "\n" not in r["display"]["variance_note"]]
    for i, ri in enumerate(rng.sample(plain, 3)):
        r = rows[ri]
        expected = {}
        for fmt, rendered in rendered_by_fmt.items():
            if fmt == "json-min":
                expected[fmt] = json.dumps({c: r["display"][c] for c in cols},
                                           separators=(",", ":"), ensure_ascii=False)
            else:
                hits = [ln for ln in rendered.split("\n")
                        if r["display"]["campaign"] in ln and r["display"]["owner"] in ln]
                if len(hits) != 1:
                    continue  # wrapped-cell formats may split the row; scorer treats absent as N/A
                expected[fmt] = hits[0]
        qs.append({
            "qid": f"{fid}-quote-{i}", "fid": fid, "family": "quote", "deposit": deposit,
            "question": (f"Find the row for campaign {r['display']['campaign']} (owner "
                         f"{r['display']['owner']}) and quote it exactly as it appears in the "
                         f"file so I can Ctrl-F it in the original sheet."),
            "expected_line_by_format": expected,
        })
    return qs


# ── S4: burial doc ───────────────────────────────────────────────────

FILLER = [
    "The quarter opened against a soft comparative, with linear impacts tracking {pct} below "
    "the equivalent week last year and digital continuing to absorb the difference.",
    "Agency conversations through {month} centred on audience guarantees, with two of the "
    "larger holding groups seeking earlier sight of regional delivery.",
    "The trading committee reviewed the position on {day} and agreed no change to the "
    "published pricing calendar.",
    "Sponsorship renewals remain ahead of plan, though the pipeline for {month} carries two "
    "unresolved approvals that could move revenue between quarters.",
    "Regional performance continued to diverge, with the northern macros holding share while "
    "the southern macros traded {pct} adrift of deal.",
    "Production credits from the {month} schedule change have been applied, and the makegood "
    "position is materially closed.",
    "The forecast assumes no further schedule disruption and holds the airtime mix constant "
    "through the remainder of the year.",
]
SECTION_HEADS = [
    "Executive summary", "Market context", "Revenue by channel", "Agency and client movements",
    "Risk register", "Sponsorship and partnerships", "Digital and streaming", "Pricing position",
    "Regional trading", "Forecast assumptions", "Weekly spend detail", "Makegoods and credits",
    "Q4 outlook", "Appendix: methodology",
]


def make_doc(rng: random.Random, table_body: str, table_fmt: str) -> str:
    parts = ["# Q3 Trading Review — internal working notes\n"]
    for i, head in enumerate(SECTION_HEADS, 1):
        parts.append(f"\n## {i}. {head}\n")
        if head == "Risk register":
            risk_cols = ["risk", "owner", "likelihood", "impact"]
            risk_rows = [{"risk": t, "owner": rng.choice(OWNER_LAST), "likelihood": rng.choice(
                ["Low", "Medium", "High"]), "impact": rng.choice(["Low", "Medium", "High"])}
                for t in ["Schedule disruption", "Holding-group consolidation",
                          "Measurement transition", "Macro softness", "Sports overrun"]]
            parts.append("\n" + R.render("md-min", risk_cols, risk_rows, set()) + "\n")
        if head == "Weekly spend detail":
            parts.append("\nThe full weekly position by campaign follows.\n\n")
            if table_fmt == "md-min":
                parts.append(table_body)
            else:
                parts.append("```\n" + table_body + "```\n")
            continue
        for _ in range(rng.randint(2, 4)):
            para = " ".join(rng.choice(FILLER).format(
                pct=f"{rng.randint(1, 9)}%", month=rng.choice(
                    ["June", "July", "August", "September"]),
                day=f"{rng.randint(1, 28)} July") for _ in range(rng.randint(3, 5)))
            parts.append(para + "\n\n")
    return "".join(parts)


# ── S5: join ─────────────────────────────────────────────────────────


def make_targets(rng: random.Random, rows: list[dict]):
    totals = {reg: sum(r["spend_val"] for r in rows if r["region"] == reg) for reg in REGIONS}
    trows, exceeded = [], []
    for reg in REGIONS:
        target = (totals[reg] * Decimal(rng.randint(85, 115)) / 100).quantize(Decimal("0.01"))
        trows.append({"display": {"region": reg, "target_spend": grp(target),
                                  "basis": rng.choice(["Deal", "Stretch", "Deal", "Reforecast"])},
                      "target": target})
        if totals[reg] > target:
            exceeded.append(reg)
    assert 2 <= len(exceeded) <= 10, f"degenerate target split: {len(exceeded)}"
    return trows, totals, exceeded


# ── main build ───────────────────────────────────────────────────────


def build(out: Path, seed: int):
    answers: list[dict] = []
    fixtures = out / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)

    # S1 at each scale (+ sabotaged variant of the 2000 tier)
    for scale in S1_SCALES:
        fid = f"s1-{scale}"
        rng = random.Random(seed + scale)
        rows = make_campaign_rows(rng, scale)
        title = "Regional Media Costings 2026"
        deposit = f"sheet--{slugify(title)}--{drive_id(rng)[:12]}"
        drive = drive_id(rng)
        rendered_by_fmt = {}
        for fmt in R.FORMATS:
            rend = write_sheet_deposit(fixtures / fid, fmt, deposit, title, drive,
                                       [("Campaigns", S1_COLS, rows, S1_NUMERIC)], rng)
            rendered_by_fmt[fmt] = rend["Campaigns"]  # dual: aligned face
        answers += s1_questions(fid, deposit, rows, random.Random(seed * 7 + scale),
                                rendered_by_fmt)

        if scale == 2000:
            # Sabotage control: header LABELS permuted, cell data and column order
            # untouched (the markitdown scrambled-header fingerprint). Implemented by
            # re-rendering with relabelled columns so every format is sabotaged
            # uniformly — no post-hoc string surgery.
            sab = f"{fid}-sabotaged"
            perm = [3, 6, 0, 7, 2, 4, 1, 5]  # fixed derangement of the 8 header slots (no fixed points)
            labels = {c: S1_COLS[p] for c, p in zip(S1_COLS, perm)}
            sab_cols = [labels[c] for c in S1_COLS]
            sab_rows = [{"display": {labels[c]: r["display"][c] for c in S1_COLS}}
                        for r in rows]
            sab_numeric = {labels[c] for c in S1_NUMERIC}
            srng = random.Random(seed + scale)  # same manifest randomness as the original
            for fmt in R.FORMATS:
                write_sheet_deposit(fixtures / sab, fmt, deposit, title, drive,
                                    [("Campaigns", sab_cols, sab_rows, sab_numeric)], srng)

    # S2 wide
    rng = random.Random(seed + 777)
    fid = "s2-wide"
    cols, numeric, rows = make_wide(rng)
    title = "FY26 Campaign Grid"
    deposit = f"sheet--{slugify(title)}--{drive_id(rng)[:12]}"
    drive = drive_id(rng)
    rendered_by_fmt = {}
    for fmt in R.FORMATS:
        rend = write_sheet_deposit(fixtures / fid, fmt, deposit, title, drive,
                                   [("Grid", cols, rows, numeric)], rng,
                                   wrap={"variance_note": 24, "notes": 28})
        rendered_by_fmt[fmt] = rend["Grid"]
    answers += s2_questions(fid, deposit, cols, rows, random.Random(seed * 11), rendered_by_fmt)

    # S4 burial: the s1-500 table embedded deep in a long doc
    rng = random.Random(seed + 4444)
    fid = "s4-buried"
    rows500 = make_campaign_rows(random.Random(seed + 500), 500)  # same as s1-500
    title = "Q3 Trading Review"
    deposit = f"doc--{slugify(title)}--{drive_id(rng)[:12]}"
    drive = drive_id(rng)
    spend_section = SECTION_HEADS.index("Weekly spend detail") + 1
    risk_section = SECTION_HEADS.index("Risk register") + 1
    for fmt in R.FORMATS:
        body_fmt = "aligned" if fmt == "dual" else fmt
        table_body = R.render(body_fmt, S1_COLS, [r["display"] for r in rows500], S1_NUMERIC)
        doc = make_doc(random.Random(seed + 4444), table_body, body_fmt)
        dep = fixtures / fid / fmt / ".mise" / deposit
        dep.mkdir(parents=True, exist_ok=True)
        (dep / "content.md").write_text(doc)
        write_manifest(dep, title, drive, [], rng, doc=True)
    dr = random.Random(seed * 13)
    deep_rows = dr.sample(range(300, 450), 3)
    answers += [
        {"qid": f"{fid}-locate-0", "fid": fid, "family": "locate", "deposit": deposit,
         "question": "Which numbered section contains the weekly spend table? Give the section "
                     "number and its heading.",
         "answer_string": f"{spend_section}. Weekly spend detail",
         "accept_number": str(spend_section)},
        {"qid": f"{fid}-locate-1", "fid": fid, "family": "locate", "deposit": deposit,
         "question": "Which numbered section contains the risk register table? Give the section "
                     "number and its heading.",
         "answer_string": f"{risk_section}. Risk register", "accept_number": str(risk_section)},
        {"qid": f"{fid}-locate-2", "fid": fid, "family": "locate", "deposit": deposit,
         "question": "Which numbered section states the forecast assumptions? Give the section "
                     "number and its heading.",
         "answer_string": f"{SECTION_HEADS.index('Forecast assumptions') + 1}. Forecast assumptions",
         "accept_number": str(SECTION_HEADS.index("Forecast assumptions") + 1)},
    ]
    for i, ri in enumerate(deep_rows):
        r = rows500[ri]
        answers.append({
            "qid": f"{fid}-lookup-{i}", "fid": fid, "family": "lookup", "deposit": deposit,
            "question": (f"In the document's weekly spend table, what is the spend figure for "
                         f"campaign {r['display']['campaign']} in week {r['display']['week']}? "
                         f"Exact figure."),
            "answer_decimal": str(r["spend_val"]), "answer_display": r["display"]["spend"],
        })

    # S5 join: campaigns (500 rows, distinct seed) + targets tab
    rng = random.Random(seed + 5555)
    fid = "s5-join"
    rowsj = make_campaign_rows(random.Random(seed + 55), 500)
    trows, totals, exceeded = make_targets(rng, rowsj)
    title = "Regional Plan vs Target 2026"
    deposit = f"sheet--{slugify(title)}--{drive_id(rng)[:12]}"
    drive = drive_id(rng)
    tcols = ["region", "target_spend", "basis"]
    for fmt in R.FORMATS:
        write_sheet_deposit(fixtures / fid, fmt, deposit, title, drive,
                            [("Campaigns", S1_COLS, rowsj, S1_NUMERIC),
                             ("Targets", tcols, trows, {"target_spend"})], rng)
    answers.append({
        "qid": f"{fid}-joinset-0", "fid": fid, "family": "joinset", "deposit": deposit,
        "question": ("Comparing the Campaigns tab against the Targets tab: which regions' total "
                     "spend across all campaigns and weeks exceeded their target? List the "
                     "regions."),
        "answer_set": sorted(exceeded),
    })
    jr = random.Random(seed * 17)
    for i, reg in enumerate(jr.sample(REGIONS, 3)):
        target = next(t["target"] for t in trows if t["display"]["region"] == reg)
        answers.append({
            "qid": f"{fid}-joingap-{i}", "fid": fid, "family": "joingap", "deposit": deposit,
            "question": (f"For region {reg}: what is the total spend across all campaigns and "
                         f"weeks minus the target in the Targets tab? Give the signed "
                         f"difference, exact."),
            "answer_decimal": str((totals[reg] - target).quantize(Decimal("0.01"))),
        })

    ans_dir = out / "answers"
    ans_dir.mkdir(exist_ok=True)
    (ans_dir / "answers.json").write_text(json.dumps(
        {"seed": seed, "generated": FETCHED_AT, "questions": answers}, indent=1))
    print(f"fixtures: {len(list(fixtures.iterdir()))} slice dirs x {len(R.FORMATS)} formats; "
          f"questions: {len(answers)}; answers OUTSIDE all world dirs at {ans_dir}/answers.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "bench-work"))
    ap.add_argument("--seed", type=int, default=SEED_DEFAULT)
    a = ap.parse_args()
    build(Path(a.out).expanduser(), a.seed)
