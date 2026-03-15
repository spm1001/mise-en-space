"""
HTTP clients for Google APIs.

Two clients sharing the same auth pattern:

- MiseHttpClient (async) — for when the full chain is async (Phase 2)
- MiseSyncClient (sync) — for migrating adapters one at a time while
  the tools/server layer stays sync (Phase 1)

Both use jeton credentials, orjson response parsing, and httpx connection pooling.
When all adapters are migrated and the tools layer goes async, MiseSyncClient
gets removed and everything uses MiseHttpClient.

Usage (Phase 1 — sync adapters):
    from adapters.http_client import get_sync_client

    def fetch_doc(doc_id: str) -> dict:
        client = get_sync_client()
        return client.get_json(
            f"https://docs.googleapis.com/v1/documents/{doc_id}"
        )
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
import orjson
from google.auth.transport.requests import Request as GoogleAuthRequest

from jeton import load_credentials
from oauth_config import TOKEN_FILE, SCOPES

# Match existing timeout in adapters/services.py
API_TIMEOUT = 60


class MiseHttpClient:
    """Async HTTP client with Google OAuth and orjson parsing.

    Thread-safe for concurrent async use. One instance per process
    (single-user architecture — matches existing lru_cache(maxsize=1) pattern).
    """

    def __init__(self, timeout: int = API_TIMEOUT) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            http2=True,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
        )
        self._credentials = load_credentials(TOKEN_FILE, scopes=SCOPES)
        if self._credentials is None:
            raise FileNotFoundError(
                f"{TOKEN_FILE} not found or invalid. Run: uv run python -m auth"
            )

    def _ensure_valid_token(self) -> None:
        """Refresh the access token if expired.

        Sync call — token refresh is one HTTP request (~100ms), happens
        once per hour. Not worth the complexity of async wrapping.
        """
        if not self._credentials.valid:
            self._credentials.refresh(GoogleAuthRequest())

    def _auth_headers(self) -> dict[str, str]:
        """Get Authorization header with current token."""
        self._ensure_valid_token()
        return {"Authorization": f"Bearer {self._credentials.token}"}

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make an authenticated request. Raises httpx.HTTPStatusError on 4xx/5xx."""
        req_headers = self._auth_headers()
        if content_type:
            req_headers["Content-Type"] = content_type
        if headers:
            req_headers.update(headers)

        kwargs: dict[str, Any] = {"headers": req_headers}
        if params:
            kwargs["params"] = params
        if json_body is not None:
            # orjson serializes, set content-type manually
            kwargs["content"] = orjson.dumps(json_body)
            kwargs["headers"]["Content-Type"] = "application/json"
        elif content is not None:
            kwargs["content"] = content

        response = await self._client.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """GET and parse response as JSON via orjson."""
        response = await self.request("GET", url, params=params)
        return orjson.loads(response.content)

    async def post_json(
        self,
        url: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST with JSON body and parse response via orjson."""
        response = await self.request("POST", url, json_body=json_body, params=params)
        return orjson.loads(response.content)

    async def patch_json(
        self,
        url: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """PATCH with JSON body and parse response via orjson."""
        response = await self.request("PATCH", url, json_body=json_body, params=params)
        return orjson.loads(response.content)

    async def put_bytes(
        self,
        url: str,
        content: bytes,
        content_type: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """PUT raw bytes (file upload) and parse response via orjson."""
        response = await self.request(
            "PUT", url, content=content, content_type=content_type, params=params,
        )
        return orjson.loads(response.content)

    async def get_bytes(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> bytes:
        """GET and return raw response bytes (for file downloads)."""
        response = await self.request("GET", url, params=params)
        return response.content

    @asynccontextmanager
    async def stream(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> AsyncIterator[httpx.Response]:
        """Stream a large download. Use for files over ~50MB.

        Usage:
            async with client.stream(url) as response:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    file.write(chunk)
        """
        req_headers = self._auth_headers()
        async with self._client.stream(
            "GET", url, headers=req_headers, params=params,
        ) as response:
            response.raise_for_status()
            yield response

    async def delete(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> None:
        """DELETE request (no response body expected)."""
        await self.request("DELETE", url, params=params)

    async def close(self) -> None:
        """Close the underlying connection pool."""
        await self._client.aclose()


class MiseSyncClient:
    """Sync HTTP client with Google OAuth and orjson parsing.

    Phase 1 client — used during adapter migration while the tools/server
    layer is still sync. Same auth and parsing as MiseHttpClient, just
    synchronous. Remove when full chain goes async.
    """

    def __init__(self, timeout: int = API_TIMEOUT) -> None:
        self._client = httpx.Client(
            timeout=timeout,
            http2=True,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
        )
        self._credentials = load_credentials(TOKEN_FILE, scopes=SCOPES)
        if self._credentials is None:
            raise FileNotFoundError(
                f"{TOKEN_FILE} not found or invalid. Run: uv run python -m auth"
            )

    def _ensure_valid_token(self) -> None:
        """Refresh the access token if expired."""
        if not self._credentials.valid:
            self._credentials.refresh(GoogleAuthRequest())

    def _auth_headers(self) -> dict[str, str]:
        """Get Authorization header with current token."""
        self._ensure_valid_token()
        return {"Authorization": f"Bearer {self._credentials.token}"}

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make an authenticated request. Raises httpx.HTTPStatusError on 4xx/5xx."""
        req_headers = self._auth_headers()
        if content_type:
            req_headers["Content-Type"] = content_type
        if headers:
            req_headers.update(headers)

        kwargs: dict[str, Any] = {"headers": req_headers}
        if params:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["content"] = orjson.dumps(json_body)
            kwargs["headers"]["Content-Type"] = "application/json"
        elif content is not None:
            kwargs["content"] = content

        response = self._client.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """GET and parse response as JSON via orjson."""
        response = self.request("GET", url, params=params)
        return orjson.loads(response.content)

    def post_json(
        self,
        url: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST with JSON body and parse response via orjson."""
        response = self.request("POST", url, json_body=json_body, params=params)
        return orjson.loads(response.content)

    def patch_json(
        self,
        url: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """PATCH with JSON body and parse response via orjson."""
        response = self.request("PATCH", url, json_body=json_body, params=params)
        return orjson.loads(response.content)

    def put_bytes(
        self,
        url: str,
        content: bytes,
        content_type: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """PUT raw bytes (file upload) and parse response via orjson."""
        response = self.request(
            "PUT", url, content=content, content_type=content_type, params=params,
        )
        return orjson.loads(response.content)

    def get_bytes(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> bytes:
        """GET and return raw response bytes (for file downloads)."""
        response = self.request("GET", url, params=params)
        return response.content

    def delete(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> None:
        """DELETE request (no response body expected)."""
        self.request("DELETE", url, params=params)

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()


# =============================================================================
# SINGLETONS
# =============================================================================

# Async client — for Phase 2 (full async chain)
_client: MiseHttpClient | None = None


def get_http_client() -> MiseHttpClient:
    """Get the singleton async HTTP client instance."""
    global _client
    if _client is None:
        _client = MiseHttpClient()
    return _client


def clear_http_client() -> None:
    """Reset the async HTTP client (for testing or after re-auth)."""
    global _client
    _client = None


# Sync client — for Phase 1 (adapter migration while tools stay sync)
_sync_client: MiseSyncClient | None = None


def get_sync_client() -> MiseSyncClient:
    """Get the singleton sync HTTP client instance.

    Phase 1 only — used during adapter migration. When the full chain
    goes async, switch to get_http_client() and remove this.
    """
    global _sync_client
    if _sync_client is None:
        _sync_client = MiseSyncClient()
    return _sync_client


def clear_sync_client() -> None:
    """Reset the sync HTTP client (for testing or after re-auth)."""
    global _sync_client
    _sync_client = None
