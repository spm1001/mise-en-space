"""Tests for the httpx-based HTTP client wrapper."""

from pathlib import Path

import pytest
import httpx
import orjson
from unittest.mock import patch, MagicMock, PropertyMock

from adapters.http_client import (
    MiseHttpClient, get_http_client, clear_http_client,
    MiseSyncClient, get_sync_client, clear_sync_client,
    _load_and_diagnose_credentials,
)
import json


# Fake credentials for testing
def _mock_credentials():
    creds = MagicMock()
    creds.valid = True
    creds.token = "test-token-123"
    return creds


def _make_client(creds=None) -> MiseHttpClient:
    """Create an async client with mocked credentials."""
    with patch("adapters.http_client._load_and_diagnose_credentials", return_value=creds or _mock_credentials()):
        return MiseHttpClient()


def _make_sync_client(creds=None) -> MiseSyncClient:
    """Create a sync client with mocked credentials."""
    with patch("adapters.http_client._load_and_diagnose_credentials", return_value=creds or _mock_credentials()):
        return MiseSyncClient()


# =============================================================================
# AUTH
# =============================================================================

class TestAuth:
    def test_sets_bearer_header(self) -> None:
        client = _make_client()
        headers = client._auth_headers()
        assert headers["Authorization"] == "Bearer test-token-123"

    def test_refreshes_expired_token(self) -> None:
        creds = _mock_credentials()
        creds.valid = False
        client = _make_client(creds)

        client._ensure_valid_token()

        creds.refresh.assert_called_once()

    def test_skips_refresh_when_valid(self) -> None:
        creds = _mock_credentials()
        creds.valid = True
        client = _make_client(creds)

        client._ensure_valid_token()

        creds.refresh.assert_not_called()

    def test_missing_token_file_raises(self) -> None:
        """Clear error when token.json doesn't exist."""
        with patch("adapters.http_client.resolve_token_path", return_value=Path("/nonexistent/token.json")):
            with pytest.raises(FileNotFoundError, match="No OAuth token found"):
                MiseHttpClient()

    def test_corrupt_token_file_raises(self, tmp_path) -> None:
        """Clear error when token.json is corrupt."""
        bad_token = tmp_path / "token.json"
        bad_token.write_text("not json {{{")
        with patch("adapters.http_client.resolve_token_path", return_value=bad_token):
            with pytest.raises(FileNotFoundError, match="corrupt"):
                MiseHttpClient()

    def test_expired_no_refresh_token_raises(self, tmp_path) -> None:
        """Clear error when token has no refresh_token."""
        token_file = tmp_path / "token.json"
        token_file.write_text('{"token": "expired", "expiry": "2020-01-01T00:00:00Z"}')
        with patch("adapters.http_client.resolve_token_path", return_value=token_file):
            with patch("adapters.http_client.load_credentials", return_value=None):
                with pytest.raises(FileNotFoundError, match="no refresh_token"):
                    MiseHttpClient()

    def test_expired_refresh_failed_raises(self, tmp_path) -> None:
        """Clear error when refresh_token exists but refresh fails."""
        token_file = tmp_path / "token.json"
        token_file.write_text('{"token": "expired", "refresh_token": "revoked", "expiry": "2020-01-01T00:00:00Z"}')
        with patch("adapters.http_client.resolve_token_path", return_value=token_file):
            with patch("adapters.http_client.load_credentials", return_value=None):
                with pytest.raises(FileNotFoundError, match="refresh failed"):
                    MiseHttpClient()


# =============================================================================
# JSON PARSING
# =============================================================================

class TestJsonParsing:
    @pytest.mark.asyncio
    async def test_get_json_parses_with_orjson(self) -> None:
        client = _make_client()
        mock_response = httpx.Response(
            200,
            content=orjson.dumps({"title": "Test Doc", "id": "abc123"}),
            request=httpx.Request("GET", "https://example.com"),
        )

        with patch.object(client._client, "request", return_value=mock_response):
            result = await client.get_json("https://docs.googleapis.com/v1/documents/abc")

        assert result == {"title": "Test Doc", "id": "abc123"}

    @pytest.mark.asyncio
    async def test_post_json_sends_orjson_body(self) -> None:
        client = _make_client()
        mock_response = httpx.Response(
            200,
            content=orjson.dumps({"result": "ok"}),
            request=httpx.Request("POST", "https://example.com"),
        )

        with patch.object(client._client, "request", return_value=mock_response) as mock_req:
            result = await client.post_json(
                "https://docs.googleapis.com/v1/documents/abc:batchUpdate",
                json_body={"requests": [{"insertText": {"text": "hello"}}]},
            )

        assert result == {"result": "ok"}
        call_kwargs = mock_req.call_args
        assert b'"requests"' in call_kwargs.kwargs.get("content", b"")

    @pytest.mark.asyncio
    async def test_patch_json(self) -> None:
        client = _make_client()
        mock_response = httpx.Response(
            200,
            content=orjson.dumps({"name": "updated"}),
            request=httpx.Request("PATCH", "https://example.com"),
        )

        with patch.object(client._client, "request", return_value=mock_response):
            result = await client.patch_json(
                "https://www.googleapis.com/drive/v3/files/abc",
                json_body={"name": "new name"},
            )

        assert result["name"] == "updated"


# =============================================================================
# RAW BYTES
# =============================================================================

class TestRawBytes:
    @pytest.mark.asyncio
    async def test_get_bytes_returns_raw(self) -> None:
        client = _make_client()
        mock_response = httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\n",
            request=httpx.Request("GET", "https://example.com"),
        )

        with patch.object(client._client, "request", return_value=mock_response):
            result = await client.get_bytes("https://www.googleapis.com/drive/v3/files/abc")

        assert result == b"\x89PNG\r\n\x1a\n"

    @pytest.mark.asyncio
    async def test_put_bytes_for_upload(self) -> None:
        client = _make_client()
        mock_response = httpx.Response(
            200,
            content=orjson.dumps({"id": "file123"}),
            request=httpx.Request("PUT", "https://example.com"),
        )

        with patch.object(client._client, "request", return_value=mock_response) as mock_req:
            result = await client.put_bytes(
                "https://www.googleapis.com/upload/drive/v3/files/abc",
                content=b"file content",
                content_type="text/markdown",
            )

        assert result["id"] == "file123"
        call_kwargs = mock_req.call_args
        assert call_kwargs.kwargs.get("content") == b"file content"


# =============================================================================
# ERROR HANDLING
# =============================================================================

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_raises_on_4xx(self) -> None:
        client = _make_client()
        mock_response = httpx.Response(
            404,
            content=b'{"error": {"message": "not found"}}',
            request=httpx.Request("GET", "https://example.com"),
        )

        with patch.object(client._client, "request", return_value=mock_response):
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await client.get_json("https://docs.googleapis.com/v1/documents/missing")

        assert exc_info.value.response.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_on_5xx(self) -> None:
        client = _make_client()
        mock_response = httpx.Response(
            503,
            content=b"Service Unavailable",
            request=httpx.Request("GET", "https://example.com"),
        )

        with patch.object(client._client, "request", return_value=mock_response):
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await client.get_json("https://docs.googleapis.com/v1/documents/abc")

        assert exc_info.value.response.status_code == 503


# =============================================================================
# DELETE
# =============================================================================

class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_succeeds(self) -> None:
        client = _make_client()
        mock_response = httpx.Response(
            204,
            request=httpx.Request("DELETE", "https://example.com"),
        )

        with patch.object(client._client, "request", return_value=mock_response):
            await client.delete("https://www.googleapis.com/drive/v3/files/abc")


# =============================================================================
# SINGLETON
# =============================================================================

class TestSingleton:
    def test_get_http_client_returns_same_instance(self) -> None:
        clear_http_client()
        with patch("adapters.http_client._load_and_diagnose_credentials", return_value=_mock_credentials()):
            a = get_http_client()
            b = get_http_client()
        assert a is b
        clear_http_client()

    def test_clear_resets_instance(self) -> None:
        clear_http_client()
        with patch("adapters.http_client._load_and_diagnose_credentials", return_value=_mock_credentials()):
            a = get_http_client()
            clear_http_client()
            b = get_http_client()
        assert a is not b
        clear_http_client()


# =============================================================================
# RETRY INTEGRATION (httpx errors work with existing retry.py)
# =============================================================================

class TestRetryIntegration:
    def test_get_http_status_extracts_from_httpx_error(self) -> None:
        from retry import _get_http_status

        error = httpx.HTTPStatusError(
            "Not Found",
            request=httpx.Request("GET", "https://example.com"),
            response=httpx.Response(404),
        )
        assert _get_http_status(error) == 404

    def test_should_retry_on_httpx_429(self) -> None:
        from retry import _should_retry

        error = httpx.HTTPStatusError(
            "Rate Limited",
            request=httpx.Request("GET", "https://example.com"),
            response=httpx.Response(429),
        )
        assert _should_retry(error) is True

    def test_should_not_retry_on_httpx_404(self) -> None:
        from retry import _should_retry

        error = httpx.HTTPStatusError(
            "Not Found",
            request=httpx.Request("GET", "https://example.com"),
            response=httpx.Response(404),
        )
        assert _should_retry(error) is False

    def test_convert_httpx_404_to_mise_error(self) -> None:
        from retry import _convert_to_mise_error
        from models import ErrorKind

        error = httpx.HTTPStatusError(
            "Not Found",
            request=httpx.Request("GET", "https://example.com"),
            response=httpx.Response(404),
        )
        mise_error = _convert_to_mise_error(error)
        assert mise_error.kind == ErrorKind.NOT_FOUND

    def test_convert_httpx_429_to_mise_error(self) -> None:
        from retry import _convert_to_mise_error
        from models import ErrorKind

        error = httpx.HTTPStatusError(
            "Rate Limited",
            request=httpx.Request("GET", "https://example.com"),
            response=httpx.Response(429),
        )
        mise_error = _convert_to_mise_error(error)
        assert mise_error.kind == ErrorKind.RATE_LIMITED
        assert mise_error.retryable is True


# =============================================================================
# SYNC CLIENT (Phase 1 — adapter migration)
# =============================================================================

class TestSyncClient:
    def test_get_json(self) -> None:
        client = _make_sync_client()
        mock_response = httpx.Response(
            200,
            content=orjson.dumps({"title": "Test"}),
            request=httpx.Request("GET", "https://example.com"),
        )

        with patch.object(client._client, "request", return_value=mock_response):
            result = client.get_json("https://docs.googleapis.com/v1/documents/abc")

        assert result == {"title": "Test"}

    def test_post_json(self) -> None:
        client = _make_sync_client()
        mock_response = httpx.Response(
            200,
            content=orjson.dumps({"result": "ok"}),
            request=httpx.Request("POST", "https://example.com"),
        )

        with patch.object(client._client, "request", return_value=mock_response):
            result = client.post_json(
                "https://docs.googleapis.com/v1/documents/abc:batchUpdate",
                json_body={"requests": []},
            )

        assert result == {"result": "ok"}

    def test_get_bytes(self) -> None:
        client = _make_sync_client()
        mock_response = httpx.Response(
            200,
            content=b"\x89PNG",
            request=httpx.Request("GET", "https://example.com"),
        )

        with patch.object(client._client, "request", return_value=mock_response):
            result = client.get_bytes("https://www.googleapis.com/drive/v3/files/abc")

        assert result == b"\x89PNG"

    def test_raises_on_error(self) -> None:
        client = _make_sync_client()
        mock_response = httpx.Response(
            404,
            content=b'{"error": "not found"}',
            request=httpx.Request("GET", "https://example.com"),
        )

        with patch.object(client._client, "request", return_value=mock_response):
            with pytest.raises(httpx.HTTPStatusError):
                client.get_json("https://example.com/missing")

    def test_auth_header(self) -> None:
        client = _make_sync_client()
        headers = client._auth_headers()
        assert headers["Authorization"] == "Bearer test-token-123"

    def test_refreshes_expired_token(self) -> None:
        creds = _mock_credentials()
        creds.valid = False
        client = _make_sync_client(creds)

        client._ensure_valid_token()

        creds.refresh.assert_called_once()


class TestSyncSingleton:
    def test_get_sync_client_returns_same_instance(self) -> None:
        clear_sync_client()
        with patch("adapters.http_client._load_and_diagnose_credentials", return_value=_mock_credentials()):
            a = get_sync_client()
            b = get_sync_client()
        assert a is b
        clear_sync_client()

    def test_clear_resets_instance(self) -> None:
        clear_sync_client()
        with patch("adapters.http_client._load_and_diagnose_credentials", return_value=_mock_credentials()):
            a = get_sync_client()
            clear_sync_client()
            b = get_sync_client()
        assert a is not b
        clear_sync_client()


# =============================================================================
# Guest mode (MISE_TOKEN_PATH) credential loading
# =============================================================================


class TestGuestModeCredentials:
    """ADC-shaped caller-owned credential files (e.g. Cornichon's adc.json)."""

    ADC = {
        "type": "authorized_user",
        "client_id": "c.apps.googleusercontent.com",
        "client_secret": "s",
        "refresh_token": "1//r",
        "quota_project_id": "proj",
        "universe_domain": "googleapis.com",
    }

    def test_adc_file_loads_without_jeton_and_without_writeback(
        self, tmp_path, monkeypatch
    ):
        """No token/expiry fields → loads as not-yet-valid creds for lazy
        in-memory refresh. jeton must NOT be consulted (its expired-gated
        refresh misdiagnoses ADC files) and the file must remain untouched."""
        adc = tmp_path / "adc.json"
        adc.write_text(json.dumps(self.ADC))
        before = adc.read_text()
        monkeypatch.setenv("MISE_TOKEN_PATH", str(adc))
        with patch("adapters.http_client.load_credentials") as jeton_load:
            creds = _load_and_diagnose_credentials(adc)
        jeton_load.assert_not_called()
        assert creds.refresh_token == "1//r"
        assert creds.token is None
        assert adc.read_text() == before

    def test_guest_errors_point_at_embedding_app_not_self_service(
        self, tmp_path, monkeypatch
    ):
        """An agent reading this error must not be sent down the CLI-auth path."""
        monkeypatch.setenv("MISE_TOKEN_PATH", str(tmp_path / "absent.json"))
        with pytest.raises(FileNotFoundError) as exc:
            _load_and_diagnose_credentials(tmp_path / "absent.json")
        msg = str(exc.value)
        assert "embedding application" in msg
        assert "uv run" not in msg

    def test_guest_invalid_file_shape_fails_loudly(self, tmp_path, monkeypatch):
        """authorized_user requires refresh_token/client_id/client_secret."""
        bad = tmp_path / "adc.json"
        bad.write_text(json.dumps({"type": "authorized_user"}))
        monkeypatch.setenv("MISE_TOKEN_PATH", str(bad))
        with pytest.raises(FileNotFoundError) as exc:
            _load_and_diagnose_credentials(bad)
        assert "authorized_user" in str(exc.value)

    def test_personal_mode_still_rides_jeton(self, tmp_path, monkeypatch):
        """No override → the jeton path runs exactly as before."""
        monkeypatch.delenv("MISE_TOKEN_PATH", raising=False)
        tok = tmp_path / "token.json"
        tok.write_text(json.dumps({"refresh_token": "r"}))
        sentinel = MagicMock()
        with patch("adapters.http_client.load_credentials", return_value=sentinel):
            assert _load_and_diagnose_credentials(tok) is sentinel


class TestQuotaProjectHeader:
    """x-goog-user-project rides along when credentials carry a quota project."""

    def _client_with(self, quota_project):
        client = MiseSyncClient.__new__(MiseSyncClient)
        creds = MagicMock()
        creds.valid = True
        creds.token = "t"
        creds.quota_project_id = quota_project
        client._credentials = creds
        return client

    def test_header_present_for_guest_adc_creds(self):
        h = self._client_with("mit-cornichon")._auth_headers()
        assert h["x-goog-user-project"] == "mit-cornichon"

    def test_header_absent_for_personal_creds(self):
        h = self._client_with(None)._auth_headers()
        assert "x-goog-user-project" not in h


class TestStaleTokenReload:
    """A long-running server used to serve invalid_grant forever after a fresh
    token landed on disk — credentials load once at client creation, so the
    only fix was a restart (mise-zikesa). _refresh_or_reload self-heals by
    re-reading the token file when the in-memory grant is refused."""

    def _dead_creds(self):
        from google.auth.exceptions import RefreshError

        creds = _mock_credentials()
        creds.refresh_token = "dead-grant"
        creds.refresh.side_effect = RefreshError("invalid_grant: revoked")
        return creds

    def test_sync_swaps_fresh_grant_from_disk(self) -> None:
        client = _make_sync_client(self._dead_creds())
        fresh = _mock_credentials()
        fresh.refresh_token = "new-grant"
        fresh.valid = True

        with (
            patch(
                "adapters.http_client._load_and_diagnose_credentials",
                return_value=fresh,
            ),
            patch(
                "adapters.http_client.resolve_token_path",
                return_value=Path("/tmp/no-such-token.json"),
            ),
            patch("cues_util.clear_user_email_cache"),
            patch("cues_util.resolve_user_email_eager") as resolve_identity,
        ):
            client._refresh_or_reload()

        assert client._credentials is fresh
        # The new grant may be a different account — identity re-resolved
        resolve_identity.assert_called_once()

    def test_sync_same_dead_grant_raises_friendly(self) -> None:
        client = _make_sync_client(self._dead_creds())
        same = _mock_credentials()
        same.refresh_token = "dead-grant"  # disk holds the SAME revoked grant

        with (
            patch(
                "adapters.http_client._load_and_diagnose_credentials",
                return_value=same,
            ),
            patch(
                "adapters.http_client.resolve_token_path",
                return_value=Path("/tmp/no-such-token.json"),
            ),
        ):
            with pytest.raises(FileNotFoundError, match="same grant"):
                client._refresh_or_reload()

    def test_async_swaps_fresh_grant_from_disk(self) -> None:
        client = _make_client(self._dead_creds())
        fresh = _mock_credentials()
        fresh.refresh_token = "new-grant"
        fresh.valid = True

        with (
            patch(
                "adapters.http_client._load_and_diagnose_credentials",
                return_value=fresh,
            ),
            patch(
                "adapters.http_client.resolve_token_path",
                return_value=Path("/tmp/no-such-token.json"),
            ),
        ):
            client._refresh_or_reload()

        assert client._credentials is fresh

    def test_healthy_refresh_never_touches_disk(self) -> None:
        creds = _mock_credentials()
        creds.valid = False  # expired access token, healthy grant
        client = _make_sync_client(creds)

        with patch(
            "adapters.http_client._load_and_diagnose_credentials"
        ) as loader:
            client._ensure_valid_token()

        creds.refresh.assert_called_once()
        loader.assert_not_called()
