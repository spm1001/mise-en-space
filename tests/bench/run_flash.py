# /// script
# requires-python = ">=3.11"
# dependencies = ["google-genai>=1.0"]
# ///
"""Gemini Flash runner: same plans, same transcripts, different wire.

The panel's third member (Gemini 3.7 Flash, thinking at model default) is a
plain API reader — Garni's profile: it hydrates and reads, carries no query
tools — so every run is arm "inline" and the deposit rides the prompt. Rather
than teach the scorer a second transcript dialect, this runner emits the two
stream-json events score_runs.py actually reads (system/init with the model
READ BACK from the response, and a result event), so scoring, idempotent
skip-on-result and results.csv all work unchanged.

Honesty notes baked in:
  - Gemini bills thinking as output, so usage.output_tokens =
    candidates + thoughts; the split rides beside it (thoughts_tokens,
    candidates_tokens) for provenance. cached/cache_write are 0 — no
    context caching is used, every run pays the full prompt.
  - Flash token counts are SentencePiece; the Claude columns are BPE.
    Within-Flash format comparisons are sound; cross-model token
    comparisons are not (census lesson). cost_usd is left blank — price
    cards are applied at report time, never hardcoded from memory.
  - Identity matches Garni as deployed: project itv-mit-agent-spike,
    location eu, thinking unset (= model default; probe 2026-08-17 showed
    thinking fires by default — 70 thought-tokens on a trivial question).

Usage:
    uv run --script run_flash.py --plan flash-league.json [--jobs 3]
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import time
from pathlib import Path

from run_bench import build_prompt, deposit_dir, load_answers

PROJECT = "itv-mit-agent-spike"
LOCATION = "eu"
MODEL = "gemini-3.7-flash"
TIMEOUT_MS = 300_000
MAX_ATTEMPTS = 5
RETRYABLE = {429, 500, 503, 504}


def call_flash(client, prompt: str) -> tuple[str, dict, str]:
    """One generate_content call with backoff. Returns (text, usage, model_version)."""
    from google.genai import errors
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = client.models.generate_content(model=MODEL, contents=prompt)
            if resp.text is None:
                # Safety block / empty candidate — visible, not silently scored
                raise RuntimeError(f"empty response text (candidates={resp.candidates})")
            um = resp.usage_metadata
            usage = {
                "input_tokens": um.prompt_token_count or 0,
                "output_tokens": (um.candidates_token_count or 0)
                                 + (um.thoughts_token_count or 0),
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "thoughts_tokens": um.thoughts_token_count or 0,
                "candidates_tokens": um.candidates_token_count or 0,
            }
            return resp.text, usage, getattr(resp, "model_version", None) or MODEL
        except errors.APIError as e:
            last = e
            if e.code not in RETRYABLE or attempt == MAX_ATTEMPTS:
                raise
            time.sleep(10 * attempt)
        except RuntimeError:
            raise
    raise last  # unreachable; keeps type-checkers honest


def one_run(run: dict, out: Path, answers: dict, client) -> str:
    rid = run["run_id"]
    if run["arm"] != "inline":
        return f"SKIP {rid} (flash runner is inline-only)"
    tdir = out / "transcripts"
    tdir.mkdir(exist_ok=True)
    tpath = tdir / f"{rid}.jsonl"
    if tpath.exists() and '"type":"result"' in tpath.read_text(errors="replace"):
        return f"SKIP {rid} (result present)"

    q = answers[run["qid"]]
    dep = deposit_dir(out, q, run["format"], run.get("fid_override"))
    prompt = build_prompt(q, run["format"], "inline", dep)

    t0 = time.time()
    try:
        text, usage, model_version = call_flash(client, prompt)
    except Exception as e:
        (tdir / f"{rid}.err").write_text(f"{type(e).__name__}: {e}\n")
        return f"FAIL {rid} ({type(e).__name__})"
    dur_ms = int((time.time() - t0) * 1000)

    events = [
        {"type": "system", "subtype": "init", "model": model_version,
         "provider": "vertex", "project": PROJECT, "location": LOCATION},
        {"type": "result", "result": text, "usage": usage,
         "num_turns": 1, "duration_ms": dur_ms},
    ]
    tpath.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return (f"DONE {rid} dur={dur_ms // 1000}s in={usage['input_tokens']} "
            f"think={usage['thoughts_tokens']} out={usage['candidates_tokens']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "bench-work"))
    ap.add_argument("--plan", required=True)
    ap.add_argument("--jobs", type=int, default=3)
    a = ap.parse_args()
    out = Path(a.out).expanduser()
    answers = load_answers(out)
    plan = json.loads(Path(a.plan).expanduser().read_text())
    flash_runs = [r for r in plan if r["model"] == "flash"]

    from google import genai
    from google.genai import types
    client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION,
                          http_options=types.HttpOptions(timeout=TIMEOUT_MS))

    log = (out / "harness.log").open("a")
    print(f"{len(flash_runs)} flash runs (of {len(plan)} in plan), "
          f"{a.jobs} parallel; transcripts -> {out}/transcripts/")
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        for line in ex.map(lambda r: one_run(r, out, answers, client), flash_runs):
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            print(line, flush=True)
            log.write(f"{stamp} {line}\n")
            log.flush()


if __name__ == "__main__":
    main()
