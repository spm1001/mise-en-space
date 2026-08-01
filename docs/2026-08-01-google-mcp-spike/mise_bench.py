#!/usr/bin/env python3
"""Time mise's composed do_search directly, same queries as the Google bench."""
import json, sys, time
from pathlib import Path

BASE = Path('/tmp/claude-1000/-home-modha-repos-spm1001-mise-en-space/b682f8f5-a2d4-4911-a296-d67f53b6febf/scratchpad/mise-bench')
BASE.mkdir(exist_ok=True)

from tools.search import do_search  # noqa: E402  (run with PYTHONPATH=repo root)

QUERIES = ["ViewersLogic", "Region Lift", "clean room", "measurement strategy 2026"]
RUNS = 2

out = []
for q in QUERIES:
    for run in range(RUNS):
        t0 = time.monotonic()
        try:
            res = do_search(query=q, sources=['drive', 'gmail', 'calendar'],
                            max_results=20, base_path=BASE)
            dt = time.monotonic() - t0
            d = res.to_dict() if hasattr(res, 'to_dict') else vars(res)
            row = {'query': q, 'run': run, 'secs': round(dt, 2),
                   'drive': d.get('drive_count'), 'gmail': d.get('gmail_count'),
                   'calendar': d.get('calendar_count'), 'path': d.get('path'),
                   'cues': d.get('cues')}
        except Exception as e:
            dt = time.monotonic() - t0
            row = {'query': q, 'run': run, 'secs': round(dt, 2), 'error': repr(e)[:300]}
        out.append(row)
        print(json.dumps(row, default=str))

with open(BASE / 'summary.json', 'w') as f:
    json.dump(out, f, indent=1, default=str)
print(f"\nsummary -> {BASE}/summary.json")
