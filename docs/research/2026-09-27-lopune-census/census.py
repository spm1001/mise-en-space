"""Real-mail census for the inline-reply detector (mise-lopune).

Run from the repo root so the working tree is what gets imported (a script
elsewhere picks up the wheel's force-included root modules, see
understanding.md "Which code does your probe actually reach?"):

    uv run --all-extras python docs/research/2026-09-27-lopune-census/census.py 'newer_than:120d -category:promotions' 300

Prints every firing with the recovered passages, then one summary line. The
output carries real people's mail, so read it in the terminal and never commit
it: this repo is public. Errors are printed, not swallowed; a first draft
that did `except Exception: continue` reported "multi=0" for a whole run,
and the zero said nothing about the detector.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from adapters.gmail import fetch_thread, search_threads  # noqa: E402
from extractors.gmail import extract_thread_content  # noqa: E402

query, limit = sys.argv[1], int(sys.argv[2])
res = search_threads(query, max_results=limit)
ids = [t.thread_id for t in res.results]
multi = fired = 0
for tid in ids:
    try:
        thread = fetch_thread(tid)
    except Exception as e:  # a census, not production: say it and move on
        print("ERR", tid, type(e).__name__, str(e)[:100])
        continue
    if len(thread.messages) < 2:
        continue
    multi += 1
    content = extract_thread_content(thread)
    hits = [w for w in thread.warnings if "inline reply" in w]
    if hits:
        fired += 1
        block = content[content.find("**Inline replies**"):].split("\n\n---")[0]
        print(f"=== FIRED {tid} msgs={len(thread.messages)} {hits}\n{block[:1500]}\n")
print(f"threads={len(ids)} multi={multi} fired={fired}")
