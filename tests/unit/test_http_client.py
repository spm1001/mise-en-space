"""Tests for the httpx-based HTTP client wrapper."""

from pathlib import Path

import pytest
import httpx
import orjson
from unittest.mock import patch, MagicMock, PropertyMock

from adapters.http_client import (
    MiseHttpClient, get_http_client, clear_http_client,
    MiseSyncClient, get_sync_client, clear_sync_client,
)


# Fake credentials for testing
def _mock_credentials():
    creds = MagicMock()
    creds.valid = True
    creds.token = "test-token-123"
    return creds


def _make_client(creds=None) -> MiseHttpClient:
    """Create an async client with mocked credentials."""
    with patch("adapters.http_client.load_credentials", return_value=creds or _mock_credentials()):
        return MiseHttpClient()


def _make_sync_client(creds=None) -> MiseSyncClient:
    """Create a sync client with mocked credentials."""
    with patch("adapters.http_client.load_credentials", return_value=creds or _mock_credentials()):
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
        with patch("adapters.http_client.load_credentials", return_value=_mock_credentials()):
            a = get_http_client()
            b = get_http_client()
        assert a is b
        clear_http_client()

    def test_clear_resets_instance(self) -> None:
        clear_http_client()
        with patch("adapters.http_client.load_credentials", return_value=_mock_credentials()):
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
        with patch("adapters.http_client.load_credentials", return_value=_mock_credentials()):
            a = get_sync_client()
            b = get_sync_client()
        assert a is b
        clear_sync_client()

    def test_clear_resets_instance(self) -> None:
        clear_sync_client()
        with patch("adapters.http_client.load_credentials", return_value=_mock_credentials()):
            a = get_sync_client()
            clear_sync_client()
            b = get_sync_client()
        assert a is not b
        clear_sync_client()
