"""
Tests for adapters/gmail_browser.py — a-family URL resolution via CDP.

The CDP loop itself is exercised live (it needs a real Chrome); these cover
the pure edges: response validation, endpoint selection, and the fail-open
import guard. The DOM is an outside system, so _parse_resolution is the
boundary that keeps a mangled page from minting a bogus thread id.
"""

import json

import pytest

from adapters.gmail_browser import (
    BrowserResolution,
    _candidate_endpoints,
    _parse_resolution,
    resolve_gmail_url_via_browser,
)


class TestParseResolution:
    def test_valid_payload(self):
        raw = json.dumps({"thread_id": "19fd641a90e83369",
                          "subject": "Typical client and agency contract samples"})
        result = _parse_resolution(raw)
        assert result == BrowserResolution(
            thread_id="19fd641a90e83369",
            subject="Typical client and agency contract samples",
        )

    def test_missing_subject_degrades_to_empty(self):
        result = _parse_resolution(json.dumps({"thread_id": "19fd641a90e83369"}))
        assert result is not None
        assert result.subject == ""

    @pytest.mark.parametrize("raw", [
        None,                                          # attribute not rendered yet
        "AUTH_WALL",                                   # SSO bounce sentinel
        "not json",
        json.dumps({"thread_id": "not-hex"}),          # DOM handed back garbage
        json.dumps({"thread_id": "19fd641a90e8336"}),  # 15 hex — malformed
        json.dumps({"subject": "no id at all"}),
        json.dumps({"thread_id": ""}),
    ])
    def test_rejects_everything_else(self, raw):
        assert _parse_resolution(raw) is None


class TestCandidateEndpoints:
    def test_env_endpoints_come_first(self, monkeypatch):
        monkeypatch.setenv("MISE_CDP_ENDPOINT", "http://example.test:9000/")
        monkeypatch.setenv("PASSE_CDP", "http://localhost:9223")
        endpoints = _candidate_endpoints()
        assert endpoints[0] == "http://example.test:9000"  # trailing slash stripped
        assert endpoints[1] == "http://localhost:9223"
        # the deduped default doesn't reappear
        assert endpoints.count("http://localhost:9223") == 1
        assert "http://localhost:9222" in endpoints

    def test_defaults_without_env(self, monkeypatch):
        monkeypatch.delenv("MISE_CDP_ENDPOINT", raising=False)
        monkeypatch.delenv("PASSE_CDP", raising=False)
        assert _candidate_endpoints() == [
            "http://localhost:9223", "http://localhost:9222",
        ]


class TestFailOpen:
    def test_no_live_endpoint_returns_none(self, monkeypatch):
        # Point everything at a port nothing listens on — the resolver must
        # come back None quickly, never raise.
        monkeypatch.setenv("MISE_CDP_ENDPOINT", "http://127.0.0.1:1")
        monkeypatch.setenv("PASSE_CDP", "http://127.0.0.1:1")
        import adapters.gmail_browser as gb
        monkeypatch.setattr(
            gb, "_candidate_endpoints", lambda: ["http://127.0.0.1:1"]
        )
        assert resolve_gmail_url_via_browser(
            "https://mail.google.com/mail/u/0/#all/KtbxLwghjwWScTGNNHctnzRVJkLPKbVvSB"
        ) is None
