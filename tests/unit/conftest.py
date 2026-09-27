"""Unit tests run hermetically: no machine credential may influence a green.

CI has no OAuth token, so any unit test whose pass depends on the
developer's personal token diverges silently: green on every dev machine,
red on every CI run. That happened 2026-08-09→12 — thread_web_link_or_warn
resolves the user's identity from the token file, an attachment test
asserted 'no warnings at all', and CI sat red for sixteen runs across
three suite publishes while every local suite stayed green (the story is
on mise-wahane's close note; the standing rule it violated is traps.md's
'read the CI result before you write the verdict').

MISE_TOKEN_PATH is authoritative when set — no Keychain fallback, by
guest-mode design (token_store docstring) — so pointing it at an absent
file makes every unit test stand exactly where CI stands. The identity
cache in cues_util is cleared around each test for the same reason: a
value resolved under one test's patches must not leak into the next.

Tests that exercise credential loading itself set the env they mean with
monkeypatch; this default only removes the AMBIENT machine credential.
"""

import pytest

import cues_util


@pytest.fixture(autouse=True)
def _hermetic_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "MISE_TOKEN_PATH", str(tmp_path / "hermetic-absent-token.json")
    )
    # The OAuth-client seam (mise-nujina) is ambient too: a shell or MCP env
    # carrying MISE_OAUTH_CLIENT / MISE_DATA_DIR must not steer a unit test.
    monkeypatch.delenv("MISE_OAUTH_CLIENT", raising=False)
    monkeypatch.delenv("MISE_DATA_DIR", raising=False)
    # And its pre-seam stores point nowhere, so a broken seam guard can never
    # adopt the developer's REAL token into a test — green on a dev box, a
    # different world on CI. Found by a mutation control that reddened three
    # test_token_store tests it was never aimed at (2026-09-27).
    monkeypatch.setattr(
        "token_store.PRE_SEAM_DATA_DIRS", (tmp_path / "hermetic-no-pre-seam",))
    monkeypatch.setattr(
        "token_store.PRE_SEAM_KEYCHAIN_SERVICES", ("mise-hermetic-absent",))
    cues_util.clear_user_email_cache()
    yield
    cues_util.clear_user_email_cache()


@pytest.fixture(autouse=True)
def _single_tab_doc_guard(monkeypatch):
    """Overwrite's multi-tab guard (mise-wisuzu) reads documents.get before
    any doc overwrite and FAILS CLOSED on an unexpected error — so every
    unmocked doc-path overwrite test would refuse instead of exercising its
    subject. Default the read to a single-tab answer.

    Vacuous-by-construction warning (the test_edit.py restore-point stub's
    sibling): with this in place, no test outside test_doc_tabs.py can see
    the guard at all. Any assertion about the guard's behaviour belongs in
    tests/unit/test_doc_tabs.py::TestOverwriteMultiTabGuard, which patches
    over this stub explicitly."""
    monkeypatch.setattr(
        "tools.overwrite.get_doc_tabs_meta",
        lambda file_id: {
            "title": "Test Doc",
            "tabs": [{"tab_id": "t.0", "title": "Tab 1", "index": 0, "depth": 0}],
        },
    )


@pytest.fixture(autouse=True)
def _fresh_draft_write_registry():
    """tools.draft remembers mise's last write per draft id (mise-zefele); a
    value left by one test would read as 'changed under us' in the next."""
    from tools.draft import _LAST_WRITTEN
    _LAST_WRITTEN.clear()
    yield
    _LAST_WRITTEN.clear()
