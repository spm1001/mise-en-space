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
