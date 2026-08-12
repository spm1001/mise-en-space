"""Ambient ADC mode (mise-wasagu): opt-in, scope tiers, loader, refusals.

The hermetic conftest sets MISE_TOKEN_PATH for every unit test, so ambient
tests here delenv it first — and the one test that deliberately leaves it
in place is pinning the mutual-exclusion error itself.
"""

from unittest.mock import MagicMock, patch

import pytest

from oauth_config import (
    AMBIENT_SCOPES_READONLY,
    AMBIENT_SCOPES_READWRITE,
    ambient_scopes,
)
from token_store import ambient_mode
from adapters.http_client import _load_and_diagnose_credentials
from tools.dispatch import run_operation


@pytest.fixture
def ambient_env(monkeypatch):
    """Clean ambient opt-in: no token-file override, MISE_CREDENTIALS set."""
    monkeypatch.delenv("MISE_TOKEN_PATH", raising=False)
    monkeypatch.setenv("MISE_CREDENTIALS", "ambient")


class TestAmbientMode:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("MISE_TOKEN_PATH", raising=False)
        monkeypatch.delenv("MISE_CREDENTIALS", raising=False)
        assert ambient_mode() is False

    def test_empty_value_is_off(self, monkeypatch):
        monkeypatch.delenv("MISE_TOKEN_PATH", raising=False)
        monkeypatch.setenv("MISE_CREDENTIALS", "")
        assert ambient_mode() is False

    def test_opt_in(self, ambient_env):
        assert ambient_mode() is True

    def test_unknown_value_is_loud(self, monkeypatch):
        """A typo'd value must never be accepted-and-dropped — this
        codebase's characteristic bug (understanding.md)."""
        monkeypatch.delenv("MISE_TOKEN_PATH", raising=False)
        monkeypatch.setenv("MISE_CREDENTIALS", "Ambient")
        with pytest.raises(ValueError, match="MISE_CREDENTIALS.*'ambient'"):
            ambient_mode()

    def test_both_modes_set_is_an_error_not_a_precedence(self, monkeypatch):
        """Guest file and ambient ADC name different identities; picking a
        winner silently would be an identity switch. The hermetic conftest
        has already set MISE_TOKEN_PATH — deliberately kept here."""
        monkeypatch.setenv("MISE_CREDENTIALS", "ambient")
        with pytest.raises(ValueError, match="Unset one"):
            ambient_mode()


class TestAmbientScopes:
    def test_default_is_readwrite(self, monkeypatch):
        monkeypatch.delenv("MISE_SCOPES", raising=False)
        assert ambient_scopes() == AMBIENT_SCOPES_READWRITE

    def test_readonly_tier(self, monkeypatch):
        monkeypatch.setenv("MISE_SCOPES", "readonly")
        scopes = ambient_scopes()
        assert scopes == AMBIENT_SCOPES_READONLY
        assert all("readonly" in s for s in scopes)

    def test_explicit_readwrite(self, monkeypatch):
        monkeypatch.setenv("MISE_SCOPES", "readwrite")
        assert ambient_scopes() == AMBIENT_SCOPES_READWRITE

    def test_unknown_tier_is_loud(self, monkeypatch):
        monkeypatch.setenv("MISE_SCOPES", "read-only")
        with pytest.raises(ValueError, match="MISE_SCOPES"):
            ambient_scopes()

    def test_no_user_context_scope_in_either_tier(self):
        """Design constraint: a service account has no mailbox or personal
        calendar, so NO gmail/calendar scope may ever ride ambient mode —
        the ops refuse instead (tools/dispatch.py gate)."""
        for scope in AMBIENT_SCOPES_READWRITE + AMBIENT_SCOPES_READONLY:
            assert "gmail" not in scope
            assert "calendar" not in scope


class TestAmbientLoader:
    """Through the real seam: _load_and_diagnose_credentials."""

    def test_ambient_routes_to_adc_with_readwrite_scopes(self, ambient_env):
        sentinel = MagicMock(name="ambient-creds")
        with patch("google.auth.default", return_value=(sentinel, None)) as mock_adc:
            creds = _load_and_diagnose_credentials("/ignored/by/ambient/path.json")
        assert creds is sentinel
        mock_adc.assert_called_once_with(scopes=AMBIENT_SCOPES_READWRITE)

    def test_readonly_tier_reaches_the_mint(self, ambient_env, monkeypatch):
        monkeypatch.setenv("MISE_SCOPES", "readonly")
        sentinel = MagicMock(name="ambient-creds")
        with patch("google.auth.default", return_value=(sentinel, None)) as mock_adc:
            _load_and_diagnose_credentials("/ignored.json")
        mock_adc.assert_called_once_with(scopes=AMBIENT_SCOPES_READONLY)

    def test_no_adc_found_teaches(self, ambient_env):
        from google.auth.exceptions import DefaultCredentialsError

        with patch("google.auth.default", side_effect=DefaultCredentialsError("nope")):
            with pytest.raises(FileNotFoundError) as exc:
                _load_and_diagnose_credentials("/ignored.json")
        msg = str(exc.value)
        assert "MISE_CREDENTIALS=ambient" in msg
        assert "GOOGLE_APPLICATION_CREDENTIALS" in msg
        assert "metadata server" in msg

    def test_missing_token_never_falls_back_to_adc(self, monkeypatch, tmp_path):
        """THE no-silent-fallback negative: without the opt-in, a missing
        token must keep teaching re-auth — google.auth.default must not
        even be consulted. Falling through would silently switch identity
        to whatever ADC the machine happens to hold."""
        monkeypatch.delenv("MISE_CREDENTIALS", raising=False)
        monkeypatch.setenv("MISE_TOKEN_PATH", str(tmp_path / "absent.json"))
        with patch("google.auth.default") as mock_adc:
            with pytest.raises(FileNotFoundError, match="No OAuth token found"):
                _load_and_diagnose_credentials(tmp_path / "absent.json")
        mock_adc.assert_not_called()


class TestAmbientDispatchGate:
    @pytest.mark.parametrize(
        "op", ["draft", "reply_draft", "archive", "star", "label", "respond", "setup_oauth"]
    )
    def test_mailbox_ops_refuse_with_the_reason(self, ambient_env, op):
        result = run_operation(op, {})
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "ambient (service-account) mode" in result["message"]

    def test_gate_fires_before_param_validation(self, ambient_env):
        """The refusal must name the mode, not complain about missing
        params — the caller's next move is a different op, not more args."""
        result = run_operation("draft", {})
        assert "requires" not in result["message"]

    def test_drive_ops_pass_the_gate(self, ambient_env):
        """create proceeds to ordinary validation — proving the gate is a
        list, not a mode-wide lockout."""
        result = run_operation("create", {})
        assert "ambient" not in result.get("message", "")

    def test_normal_mode_is_untouched(self, monkeypatch):
        monkeypatch.delenv("MISE_CREDENTIALS", raising=False)
        result = run_operation("draft", {})
        assert "ambient" not in result.get("message", "")
