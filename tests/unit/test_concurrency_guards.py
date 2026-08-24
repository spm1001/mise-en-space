"""The three guards that replaced the tool-body serializer (mise-bapije).

Each test hammers its guard with real threads and includes the failure it
prevents in its docstring, so a future reader knows what breaks if the guard
goes. The old serializer made all three unreachable-by-construction; with it
deleted, these are the tests that hold the line.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from workspace.manager import write_search_results


# ---------------------------------------------------------------------------
# Guard 1: O_EXCL search-deposit naming (workspace/manager.py)
# ---------------------------------------------------------------------------

def test_search_deposit_names_survive_a_same_second_stampede(tmp_path: Path) -> None:
    """Eight threads, identical query+sources, same wall-clock second — the
    exists()-check era would let two threads claim one filename and the second
    write destroy the first (the TOCTOU face of mise-gemowa). O_EXCL must
    yield eight DISTINCT files, every payload intact."""
    n = 8

    def one(i: int) -> Path:
        return write_search_results(
            "stampede probe", {"marker": i}, sources=["drive"], base_path=tmp_path
        )

    with ThreadPoolExecutor(max_workers=n) as pool:
        paths = list(pool.map(one, range(n)))

    assert len(set(paths)) == n, f"filename collision: {sorted(set(paths))}"
    markers = sorted(json.loads(p.read_text())["marker"] for p in paths)
    assert markers == list(range(n)), f"lost or duplicated payloads: {markers}"


# ---------------------------------------------------------------------------
# Guard 2: per-resource deposit lock on the fetch dispatch (tools/fetch/router.py)
# ---------------------------------------------------------------------------

def _dispatch_recorder(events: list, monkeypatch) -> None:
    """Replace the drive handler with a sleeping recorder."""
    from tools.fetch import router

    def fake_fetch_drive(resource_id, **kwargs):
        events.append(("start", resource_id, time.perf_counter()))
        time.sleep(0.15)
        events.append(("end", resource_id, time.perf_counter()))
        from models import FetchResult
        return FetchResult(path="/tmp/x", content_file="/tmp/x/content.md",
                           format="markdown", type="doc", metadata={})

    monkeypatch.setattr(router, "fetch_drive", fake_fetch_drive)


def _overlap(events: list, id_a: str, id_b: str) -> bool:
    spans = {}
    for kind, rid, t in events:
        spans.setdefault(rid, {})[kind] = t
    a, b = spans[id_a], spans[id_b]
    return a["start"] < b["end"] and b["start"] < a["end"]


def test_same_id_fetches_serialize_different_ids_parallel(monkeypatch) -> None:
    """Two concurrent fetches of the SAME id share a deposit folder, and the
    wipe-on-refetch in get_deposit_folder would eat the sibling's in-flight
    writes (a chimera deposit when options differ). Same id must queue;
    different ids — the CC parallel-hydration case the serializer lift exists
    for — must overlap."""
    from tools.fetch.router import do_fetch

    same = "1AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    other = "1BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"

    # Arm A: same id twice -> no overlap
    events: list = []
    _dispatch_recorder(events, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: do_fetch(same, base_path=Path("/tmp")), range(2)))
    kinds = [k for k, _, _ in events]
    assert kinds == ["start", "end", "start", "end"], (
        f"same-id fetches interleaved: {kinds} — the deposit wipe can now eat "
        "a sibling's in-flight writes"
    )

    # Arm B: two different ids -> overlap (the perf case must not regress)
    events.clear()
    with ThreadPoolExecutor(max_workers=2) as pool:
        pool.submit(do_fetch, same, base_path=Path("/tmp"))
        pool.submit(do_fetch, other, base_path=Path("/tmp"))
    assert _overlap(events, same, other), (
        f"different-id fetches serialized: {events} — the per-resource lock "
        "is too coarse; parallel hydration has silently regressed"
    )


# ---------------------------------------------------------------------------
# Guard 3: single-flight token refresh (adapters/http_client.py)
# ---------------------------------------------------------------------------

class _FakeCredentials:
    """valid flips true after refresh(); refresh sleeps so a stampede window
    genuinely exists."""

    def __init__(self) -> None:
        self._valid = False
        self.refresh_count = 0
        self._count_lock = threading.Lock()

    @property
    def valid(self) -> bool:
        return self._valid

    def refresh(self, request) -> None:
        with self._count_lock:
            self.refresh_count += 1
        time.sleep(0.1)
        self._valid = True


def test_refresh_is_single_flight_across_threads() -> None:
    """Eight threads hit _ensure_valid_token on one expired credential.
    Unlocked, each runs its own refresh (Google tolerates it, but the
    dead-grant RELOAD path swaps self._credentials and re-resolves the
    identity cue — an interleaved swap there is the real hazard). Under the
    lock exactly ONE refresh must run; siblings re-check validity and skip."""
    from adapters.http_client import MiseSyncClient

    client = MiseSyncClient.__new__(MiseSyncClient)  # no network, no token file
    client._credentials = _FakeCredentials()
    client._refresh_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: client._ensure_valid_token(), range(8)))

    assert client._credentials.refresh_count == 1, (
        f"{client._credentials.refresh_count} refreshes ran — single-flight "
        "is broken; the dead-grant reload path can interleave its credential swap"
    )
    assert client._credentials.valid


# ---------------------------------------------------------------------------
# Guard 2b: lock-KEY convergence across the surfaces that touch one deposit
# (fetch dispatch vs do(source=) readers). The cold review of 2026-08-24 showed
# the guard class fails at the KEY, not the lock: two spellings of one resource
# holding two locks over one folder re-opens the wipe race.
# ---------------------------------------------------------------------------

def test_fetch_and_source_readers_converge_on_one_lock(tmp_path: Path) -> None:
    """deposit_lock_for_source(deposit-of-X) must return the SAME RLock object
    the fetch dispatch takes for X — otherwise do(create/overwrite, source=)
    and a concurrent fetch of X are 'guarded' by two different locks, which is
    no guard at all."""
    import json as _json

    from workspace.manager import deposit_lock, deposit_lock_for_source

    rid = "1XCONVERGENCE_TEST_RESOURCE_IDXXXXXXXXXXXXXX"
    dep = tmp_path / "doc--convergence--1XCONVERGENC"
    dep.mkdir()
    (dep / "manifest.json").write_text(_json.dumps({"id": rid, "type": "doc"}))

    assert deposit_lock_for_source(dep) is deposit_lock(rid), (
        "source-reader lock and fetch lock diverge for the same resource id"
    )
    # Manifest-less deposits fall back to the resolved path — still one lock
    # per folder, so two source-readers of one folder converge too.
    bare = tmp_path / "no-manifest"
    bare.mkdir()
    assert deposit_lock_for_source(bare) is deposit_lock_for_source(bare)


# ---------------------------------------------------------------------------
# Guard 4: the sync-client singleton mints exactly once under a thread burst
# ---------------------------------------------------------------------------

def test_sync_client_mint_is_single_flight(monkeypatch) -> None:
    """Eight threads race get_sync_client() on a cold singleton. The unlocked
    check-then-act minted N clients — N httpx pools, N-1 of them leaked for
    process lifetime (cold review, 2026-08-24). Locked: exactly one mint, all
    callers handed the same object."""
    import adapters.http_client as hc

    mints: list[object] = []
    mint_lock = threading.Lock()

    class _FakeClient:
        def __init__(self) -> None:
            with mint_lock:
                mints.append(self)
            time.sleep(0.05)  # hold the window open

    monkeypatch.setattr(hc, "MiseSyncClient", _FakeClient)
    monkeypatch.setattr(hc, "_sync_client", None)

    with ThreadPoolExecutor(max_workers=8) as pool:
        clients = list(pool.map(lambda _: hc.get_sync_client(), range(8)))

    monkeypatch.setattr(hc, "_sync_client", None)  # never leak the fake
    assert len(mints) == 1, f"{len(mints)} clients minted — the mint lock is broken"
    assert all(c is clients[0] for c in clients)
