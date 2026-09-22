# /// script
# requires-python = ">=3.11"
# ///
"""Score the tijeko runs against the pre-registered decision rule.

    uv run --script score.py            # tables to stdout
    uv run --script score.py --json     # machine-readable, for the results file
"""
import collections
import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scenarios import CURRENT, SCENARIOS  # noqa: E402

HERE = Path(__file__).parent
MINE = {  # the author's "want" column, predictions.md — the rule-2 tiebreak
    "C1-reply-all-plus-cc": "union", "C2-move-two-folder-ids": "refuse",
    "C3-folder-with-content": "refuse", "C4-page-setup-on-form": "ignore_warn",
    "C5-form-from-file-path": "consume", "C6-form-into-folder": "consume",
    "C7-restore-comment-on-sheet": "ignore", "C8-event-confirm-without-attendees": "ignore",
    "C9-meet-false-on-update": "partial_warn",
}


def load() -> list[dict]:
    subjects = []
    for f in sorted(glob.glob(str(HERE / "runs" / "s*.json"))):
        raw = json.load(open(f))
        model = ",".join(raw.get("modelUsage", {})) or "UNKNOWN"
        text = raw.get("result", "")
        m = re.search(r"\{.*\}", text, re.S)  # tolerate a stray fence
        body = json.loads(m.group(0)) if m else {}
        subjects.append({"file": Path(f).name, "model": model, "body": body})
    return subjects


def main() -> None:
    subjects = load()
    keys = {s["id"]: set(s["options"]) for s in SCENARIOS}
    out: dict = {"subjects": [(s["file"], s["model"]) for s in subjects], "cases": {}}
    bad = []
    for sc in SCENARIOS:
        cid = sc["id"]
        pred, pref, dist = collections.Counter(), collections.Counter(), collections.Counter()
        by_model: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        whys = []
        for s in subjects:
            a = s["body"].get("answers", {}).get(cid)
            if not a:
                bad.append((s["file"], cid, "missing"))
                continue
            for field in ("predict", "prefer"):
                if a.get(field) not in keys[cid]:
                    bad.append((s["file"], cid, f"{field}={a.get(field)!r}"))
            pred[a.get("predict")] += 1
            pref[a.get("prefer")] += 1
            by_model[s["model"]][a.get("prefer")] += 1
            for d in a.get("distrust", []):
                dist[d] += 1
            whys.append((s["model"], a.get("prefer"), a.get("why")))
        n = sum(pref.values())
        top, top_n = pref.most_common(1)[0]
        if top_n >= 5:
            decided, how = top, f"rule 1 ({top_n}/{n})"
        else:
            cands = [k for k, v in pref.items() if v >= 2]
            fewest = min(dist[k] for k in cands)
            tied = [k for k in cands if dist[k] == fewest]
            if len(tied) == 1:
                decided, how = tied[0], "rule 2 (fewest distrust)"
            else:
                decided, how = MINE[cid], f"rule 2 tie {tied} -> author's column (WEAK)"
        cur = CURRENT[cid]
        perturbing = dist[cur] >= 3 or (pred.most_common(1)[0][0] != cur and top != cur)
        out["cases"][cid] = {
            "current": cur, "predict": dict(pred), "prefer": dict(pref),
            "distrust": dict(dist), "by_model": {m: dict(c) for m, c in by_model.items()},
            "decided": decided, "how": how, "perturbing": perturbing,
            "author_matches": MINE[cid] == decided, "whys": whys,
        }
    echo = collections.Counter(
        (s["model"], s["body"].get("echo_defaults", {}).get("answer")) for s in subjects)
    out["echo_defaults"] = {f"{m}:{a}": c for (m, a), c in echo.items()}
    out["echo_whys"] = [(s["model"], s["body"].get("echo_defaults", {}).get("why")) for s in subjects]
    out["validation_problems"] = bad
    if "--json" in sys.argv:
        print(json.dumps(out, indent=2))
        return
    print("subjects:", out["subjects"])
    print("validation problems:", bad or "none")
    for cid, c in out["cases"].items():
        print(f"\n== {cid}  (today: {c['current']})")
        print("  predict :", c["predict"])
        print("  prefer  :", c["prefer"])
        print("  distrust:", c["distrust"])
        print("  by model:", c["by_model"])
        print(f"  DECIDED : {c['decided']}  via {c['how']}  | perturbing={c['perturbing']}"
              f"  | author matches={c['author_matches']}")
    print("\necho_defaults:", out["echo_defaults"])


if __name__ == "__main__":
    main()
