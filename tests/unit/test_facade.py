"""The mise_en_space facade — the blessed library door (mise-dareti).

Two families here. CONTRACT tests pin the facade against the surfaces it
fronts: method signatures against tools.do_search/do_fetch, and the do()
param dict against server.py's real signature — so a param added to the
MCP surface without a facade entry fails loudly instead of KeyError-ing
inside a dispatch handler. IDENTITY tests exercise constructor selection
end-to-end through token_store and the http_client loader.

Env discipline (the mise-wahane lesson): the unit conftest pins
MISE_TOKEN_PATH to an absent file, so tests that need a CLEAN environment
state that explicitly with monkeypatch.delenv — never by assuming the
machine. Identity state is process-global by design, so every test here
resets it on the way out.
"""

import inspect

import pytest
from google.auth.exceptions import RefreshError

import token_store
from adapters.http_client import (
    MiseSyncClient,
    _load_and_diagnose_credentials,
    clear_http_client,
    clear_sync_client,
)
from mise_en_space import _DO_DEFAULTS, Mise
from tools import do_fetch, do_search


@pytest.fixture(autouse=True)
def _reset_identity():
    """Constructor-selected identity is process-global; leave none behind."""
    yield
    token_store.configure_identity()
    clear_sync_client()
    clear_http_client()


@pytest.fixture
def clean_env(monkeypatch):
    """No identity env vars at all — the Cloud Run / library-consumer shape."""
    monkeypatch.delenv("MISE_TOKEN_PATH", raising=False)
    monkeypatch.delenv("MISE_CREDENTIALS", raising=False)


class _Sentinel:
    """A stand-in credentials object; never talks to Google."""

    valid = True
    token = "sentinel-token"


class _DeadCreds:
    """Credentials whose refresh Google refuses — the injected-refusal case."""

    valid = False
    token = None

    def refresh(self, request):
        raise RefreshError("invalid_grant: dead for the test")


class TestIdentitySelection:
    def test_two_selectors_refused(self, clean_env):
        with pytest.raises(ValueError, match="credentials and ambient"):
            token_store.configure_identity(credentials=_Sentinel(), ambient=True)

    def test_env_collision_refused(self, monkeypatch):
        # Env set (the conftest pin counts, but state it explicitly).
        monkeypatch.setenv("MISE_TOKEN_PATH", "/somewhere/token.json")
        with pytest.raises(ValueError, match="MISE_TOKEN_PATH"):
            token_store.configure_identity(credentials=_Sentinel())

    def test_bare_constructor_clears_back_to_env_default(self, clean_env, tmp_path):
        Mise(token_path=tmp_path / "t.json")
        assert token_store.override_path() == tmp_path / "t.json"
        Mise()
        assert token_store.override_path() is None

    def test_injected_credentials_win_the_loader(self, clean_env):
        creds = _Sentinel()
        Mise(credentials=creds)
        assert _load_and_diagnose_credentials("/definitely/absent.json") is creds

    def test_token_path_selection_is_guest_mode(self, clean_env, tmp_path):
        p = tmp_path / "caller-owned.json"
        Mise(token_path=p)
        assert token_store.override_path() == p
        assert token_store.resolve_token_path(tmp_path / "fallback.json") == p

    def test_ambient_selection_fires_the_ambient_gates(self, clean_env):
        from tools.dispatch import run_operation

        Mise(ambient=True)
        assert token_store.ambient_mode() is True
        # The wasagu mailbox gate must fire for constructor-ambient exactly
        # as for env-ambient — no credential is touched on this path.
        result = run_operation("draft", dict(_DO_DEFAULTS, to="x@y.z", subject="s", content="c"))
        assert result["error"] is True
        assert "ambient" in result["message"]

    def test_refresh_refusal_names_the_caller_owned_object(self, clean_env):
        Mise(credentials=_DeadCreds())
        client = MiseSyncClient()
        with pytest.raises(FileNotFoundError, match="passed to mise in code"):
            client._auth_headers()


class TestFacadeContract:
    def test_search_signature_mirrors_the_tool(self):
        facade = inspect.signature(Mise.search).parameters
        tool = inspect.signature(do_search).parameters
        facade_view = {n: p.default for n, p in facade.items() if n != "self"}
        tool_view = {n: p.default for n, p in tool.items()}
        assert facade_view == tool_view

    def test_fetch_signature_mirrors_the_tool(self):
        facade = inspect.signature(Mise.fetch).parameters
        tool = inspect.signature(do_fetch).parameters
        facade_view = {n: p.default for n, p in facade.items() if n != "self"}
        tool_view = {n: p.default for n, p in tool.items()}
        assert facade_view == tool_view

    def test_do_defaults_mirror_servers_signature(self):
        """server.py's do() is the authoritative param surface; pin to it."""
        import server

        server_params = {
            name: p.default
            for name, p in inspect.signature(server.do).parameters.items()
            if name != "operation"
        }
        assert _DO_DEFAULTS == server_params

    def test_do_refuses_unknown_params(self):
        with pytest.raises(TypeError, match="nonsense"):
            Mise().do("create", nonsense=1)

    def test_do_fills_the_full_param_dict(self, monkeypatch):
        captured = {}

        def fake_run(operation, params):
            captured["operation"] = operation
            captured["params"] = params
            return {"ok": True}

        monkeypatch.setattr("mise_en_space.run_operation", fake_run)
        Mise(base_path="/deposits").do("create", title="T")
        assert captured["operation"] == "create"
        assert set(captured["params"]) == set(_DO_DEFAULTS)
        assert captured["params"]["title"] == "T"
        assert captured["params"]["base_path"] == "/deposits"
        assert captured["params"]["doc_type"] == "doc"

    def test_do_explicit_base_path_beats_the_constructor(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "mise_en_space.run_operation",
            lambda op, params: captured.update(params) or {"ok": True},
        )
        Mise(base_path="/deposits").do("create", title="T", base_path="/elsewhere")
        assert captured["base_path"] == "/elsewhere"

    def test_fetch_threads_the_constructor_base_path(self, monkeypatch):
        captured = {}

        def fake_fetch(file_id, **kwargs):
            captured["file_id"] = file_id
            captured.update(kwargs)
            return {"ok": True}

        monkeypatch.setattr("mise_en_space.do_fetch", fake_fetch)
        from pathlib import Path

        Mise(base_path="/deposits").fetch("abc123")
        assert captured["base_path"] == Path("/deposits")
