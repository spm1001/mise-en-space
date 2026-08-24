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
