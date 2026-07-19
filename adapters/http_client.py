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

# httpx params accept dict, list-of-tuples, or QueryParams.
# list-of-tuples is needed for repeated keys (e.g. batchGet ranges).
QueryParamsType = dict[str, Any] | list[tuple[str, str]] | None

import json
import logging
from pathlib import Path

import httpx
import orjson
from google.auth.transport.requests import Request as GoogleAuthRequest

from jeton import load_credentials
from oauth_config import TOKEN_FILE, SCOPES
from token_store import resolve_token_path

logger = logging.getLogger(__name__)

# Default timeout for Google API calls
API_TIMEOUT = 60


def _bootstrap_hint(guest_mode: bool) -> str:
    """The re-auth remedy line for credential errors.

    Points at the setup_oauth tool, which is what's reachable when running
    under Claude Desktop / Cowork. The CLI fallback (`uv run python -m auth
    --auto`) is mentioned only when the bootstrap tool can't be reached.
    In guest mode (MISE_TOKEN_PATH) neither applies — the credential belongs
    to the embedding app, and an agent reading this error must be pointed
    AWAY from self-service re-auth (observed live: an agent followed the CLI
    hint into a PATH-probing expedition inside a sealed sandbox).
    """
    return (
        "This credential file belongs to the embedding application — "
        "re-authenticate there. Do not attempt setup_oauth or CLI auth."
        if guest_mode
        else "Call mise.do(operation=\"setup_oauth\") to authenticate "
        "(opens a browser; saves token to Keychain). "
        "CLI fallback: uv run python -m auth --auto"
    )


def _load_and_diagnose_credentials(token_path: str | Path) -> Any:
    """Load OAuth credentials with clear error messages for each failure mode.

    Instead of a generic "token not found" error, distinguishes:
    - Token file missing
    - Token file corrupt
    - Token expired with no refresh_token
    - Token expired and refresh failed
    """
    from token_store import override_path
    token_path = Path(token_path)
    guest_mode = override_path() is not None

    _BOOTSTRAP_HINT = _bootstrap_hint(guest_mode)

    if not token_path.exists():
        raise FileNotFoundError(
            f"No OAuth token found at {token_path}"
            + (" (also checked Keychain)" if not guest_mode else "")
            + f". {_BOOTSTRAP_HINT}"
        )

    # File exists — try to read it
    try:
        token_data = json.loads(token_path.read_text())
    except (json.JSONDecodeError, IOError) as e:
        raise FileNotFoundError(
            f"OAuth token at {token_path} is corrupt ({type(e).__name__}). "
            f"{_BOOTSTRAP_HINT}"
        )

    if guest_mode:
        # ADC-shaped authorized_user file: typically NO access token and NO
        # expiry. google-auth treats that as not-yet-valid rather than expired
        # (expired=False when expiry is absent), so jeton's expired-gated
        # refresh never fires and it returns None — misdiagnosed as a revoked
        # token while the very same file serves the embedding app fine.
        # Build credentials directly and let _ensure_valid_token refresh
        # lazily IN MEMORY. Never write back: the file is the caller's, and
        # jeton's save shape would drop fields the embedding app's ADC loader
        # requires (`type`, `quota_project_id`, `universe_domain`).
        from google.oauth2.credentials import Credentials as GoogleUserCredentials
        try:
            return GoogleUserCredentials.from_authorized_user_info(token_data)
        except ValueError as e:
            raise FileNotFoundError(
                f"Credential file at {token_path} is not a valid "
                f"authorized_user file ({e}). {_BOOTSTRAP_HINT}"
            )

    # Try loading through jeton (handles refresh automatically)
    creds = load_credentials(token_path, scopes=SCOPES)
    if creds is not None:
        return creds

    # jeton returned None — diagnose why
    has_refresh = bool(token_data.get("refresh_token"))
    has_expiry = bool(token_data.get("expiry"))

    if not has_refresh:
        raise FileNotFoundError(
            f"OAuth token at {token_path} has no refresh_token — cannot auto-refresh. "
            f"{_BOOTSTRAP_HINT}"
        )

    # Has refresh_token but load_credentials still returned None — refresh must have failed
    raise FileNotFoundError(
        f"OAuth token at {token_path} is expired and refresh failed "
        f"(refresh_token may be revoked). {_BOOTSTRAP_HINT}"
    )


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
        token_path = resolve_token_path(TOKEN_FILE)
        self._credentials = _load_and_diagnose_credentials(token_path)

    def _ensure_valid_token(self) -> None:
        """Refresh the access token if expired.

        Sync call — token refresh is one HTTP request (~100ms), happens
        once per hour. Not worth the complexity of async wrapping.
        """
        if not self._credentials.valid:
            self._refresh_or_reload()

    def _refresh_or_reload(self) -> None:
        """Refresh the access token; on a dead grant, re-read the token file.

        Credentials load once at client creation, so a long-running server
        used to keep serving invalid_grant after a fresh token had already
        landed on disk via re-auth — the only fix was a restart
        (mise-zikesa). Reloading on refresh failure makes that self-healing:
        a DIFFERENT grant on disk gets swapped in; the SAME dead grant gets
        the friendly re-auth pointer.
        """
        from google.auth.exceptions import RefreshError

        try:
            self._credentials.refresh(GoogleAuthRequest())
            return
        except RefreshError:
            old_refresh = getattr(self._credentials, "refresh_token", None)
            token_path = resolve_token_path(TOKEN_FILE)
            fresh = _load_and_diagnose_credentials(token_path)
            if getattr(fresh, "refresh_token", None) == old_refresh:
                from token_store import override_path
                raise FileNotFoundError(
                    "OAuth token was refused by Google (refresh failed — "
                    f"likely revoked) and the token on disk at {token_path} "
                    "is the same grant. "
                    + _bootstrap_hint(override_path() is not None)
                    + " Once a fresh token lands, this server picks it up on "
                    "the next call — no restart needed."
                )
            self._credentials = fresh
            # A guest-mode reload may return not-yet-refreshed creds; a dead
            # NEW grant would raise RefreshError here — rare, and honest.
            if not self._credentials.valid:
                self._credentials.refresh(GoogleAuthRequest())

    def _auth_headers(self) -> dict[str, str]:
        """Get Authorization header with current token.

        Includes x-goog-user-project when the credentials carry a quota
        project (ADC-shaped guest-mode files do): user-credential calls to
        drive.googleapis.com 403 without it. Personal-mode jeton tokens have
        no quota project, so the header is absent and behaviour unchanged.
        """
        self._ensure_valid_token()
        headers = {"Authorization": f"Bearer {self._credentials.token}"}
        quota_project = getattr(self._credentials, "quota_project_id", None)
        if quota_project:
            headers["x-goog-user-project"] = quota_project
        return headers

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: QueryParamsType = None,
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

        # Retry once on 401 — see MiseSyncClient.request for rationale
        if response.status_code == 401:
            self._refresh_or_reload()
            kwargs["headers"]["Authorization"] = f"Bearer {self._credentials.token}"
            response = await self._client.request(method, url, **kwargs)

        response.raise_for_status()
        return response

    async def get_json(
        self,
        url: str,
        *,
        params: QueryParamsType = None,
    ) -> dict[str, Any]:
        """GET and parse response as JSON via orjson."""
        response = await self.request("GET", url, params=params)
        return orjson.loads(response.content)

    async def post_json(
        self,
        url: str,
        *,
        json_body: Any | None = None,
        params: QueryParamsType = None,
    ) -> dict[str, Any]:
        """POST with JSON body and parse response via orjson."""
        response = await self.request("POST", url, json_body=json_body, params=params)
        return orjson.loads(response.content)

    async def patch_json(
        self,
        url: str,
        *,
        json_body: Any | None = None,
        params: QueryParamsType = None,
    ) -> dict[str, Any]:
        """PATCH with JSON body and parse response via orjson."""
        response = await self.request("PATCH", url, json_body=json_body, params=params)
        return orjson.loads(response.content)

    async def patch_bytes(
        self,
        url: str,
        content: bytes,
        content_type: str,
        *,
        params: QueryParamsType = None,
    ) -> dict[str, Any]:
        """PATCH raw bytes (file upload/update) and parse JSON response.

        For Drive uploads where the request body is the file content
        (not JSON) but the response is JSON metadata.
        """
        response = await self.request(
            "PATCH", url, content=content, content_type=content_type, params=params,
        )
        return orjson.loads(response.content)

    async def put_bytes(
        self,
        url: str,
        content: bytes,
        content_type: str,
        *,
        params: QueryParamsType = None,
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
        params: QueryParamsType = None,
    ) -> bytes:
        """GET and return raw response bytes (for file downloads)."""
        response = await self.request("GET", url, params=params)
        return response.content

    @asynccontextmanager
    async def stream(
        self,
        url: str,
        *,
        params: QueryParamsType = None,
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
        params: QueryParamsType = None,
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
        token_path = resolve_token_path(TOKEN_FILE)
        self._credentials = _load_and_diagnose_credentials(token_path)
        # Resolve authenticated identity once, eagerly. Keeps response
        # serialisation pure — to_dict() never triggers HTTP.
        from cues_util import resolve_user_email_eager
        resolve_user_email_eager(self, token_path)

    def _ensure_valid_token(self) -> None:
        """Refresh the access token if expired."""
        if not self._credentials.valid:
            self._refresh_or_reload()

    def _refresh_or_reload(self) -> None:
        """Refresh the access token; on a dead grant, re-read the token file.

        Credentials load once at client creation, so a long-running server
        used to keep serving invalid_grant after a fresh token had already
        landed on disk via re-auth — the only fix was a restart
        (mise-zikesa). Reloading on refresh failure makes that self-healing:
        a DIFFERENT grant on disk gets swapped in; the SAME dead grant gets
        the friendly re-auth pointer.
        """
        from google.auth.exceptions import RefreshError

        try:
            self._credentials.refresh(GoogleAuthRequest())
            return
        except RefreshError:
            old_refresh = getattr(self._credentials, "refresh_token", None)
            token_path = resolve_token_path(TOKEN_FILE)
            fresh = _load_and_diagnose_credentials(token_path)
            if getattr(fresh, "refresh_token", None) == old_refresh:
                from token_store import override_path
                raise FileNotFoundError(
                    "OAuth token was refused by Google (refresh failed — "
                    f"likely revoked) and the token on disk at {token_path} "
                    "is the same grant. "
                    + _bootstrap_hint(override_path() is not None)
                    + " Once a fresh token lands, this server picks it up on "
                    "the next call — no restart needed."
                )
            self._credentials = fresh
            if not self._credentials.valid:
                self._credentials.refresh(GoogleAuthRequest())
            # The new grant may belong to a different account — re-resolve so
            # the _identity cue stays honest (best-effort inside).
            from cues_util import clear_user_email_cache, resolve_user_email_eager
            clear_user_email_cache()
            resolve_user_email_eager(self, token_path)

    def _auth_headers(self) -> dict[str, str]:
        """Get Authorization header with current token.

        Includes x-goog-user-project when the credentials carry a quota
        project (ADC-shaped guest-mode files do): user-credential calls to
        drive.googleapis.com 403 without it. Personal-mode jeton tokens have
        no quota project, so the header is absent and behaviour unchanged.
        """
        self._ensure_valid_token()
        headers = {"Authorization": f"Bearer {self._credentials.token}"}
        quota_project = getattr(self._credentials, "quota_project_id", None)
        if quota_project:
            headers["x-goog-user-project"] = quota_project
        return headers

    def request(
        self,
        method: str,
        url: str,
        *,
        params: QueryParamsType = None,
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

        # Retry once on 401 — token may report valid but be expired server-side
        # (google-auth's creds.valid only checks local expiry field, which can
        # be None for some token formats). AuthorizedHttp did this automatically.
        if response.status_code == 401:
            self._refresh_or_reload()
            kwargs["headers"]["Authorization"] = f"Bearer {self._credentials.token}"
            response = self._client.request(method, url, **kwargs)

        response.raise_for_status()
        return response

    def get_json(
        self,
        url: str,
        *,
        params: QueryParamsType = None,
    ) -> dict[str, Any]:
        """GET and parse response as JSON via orjson."""
        response = self.request("GET", url, params=params)
        return orjson.loads(response.content)

    def post_json(
        self,
        url: str,
        *,
        json_body: Any | None = None,
        params: QueryParamsType = None,
    ) -> dict[str, Any]:
        """POST with JSON body and parse response via orjson."""
        response = self.request("POST", url, json_body=json_body, params=params)
        return orjson.loads(response.content)

    def patch_json(
        self,
        url: str,
        *,
        json_body: Any | None = None,
        params: QueryParamsType = None,
    ) -> dict[str, Any]:
        """PATCH with JSON body and parse response via orjson."""
        response = self.request("PATCH", url, json_body=json_body, params=params)
        return orjson.loads(response.content)

    def patch_bytes(
        self,
        url: str,
        content: bytes,
        content_type: str,
        *,
        params: QueryParamsType = None,
    ) -> dict[str, Any]:
        """PATCH raw bytes (file upload/update) and parse JSON response."""
        response = self.request(
            "PATCH", url, content=content, content_type=content_type, params=params,
        )
        return orjson.loads(response.content)

    def put_bytes(
        self,
        url: str,
        content: bytes,
        content_type: str,
        *,
        params: QueryParamsType = None,
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
        params: QueryParamsType = None,
    ) -> bytes:
        """GET and return raw response bytes (for file downloads)."""
        response = self.request("GET", url, params=params)
        return response.content

    def delete(
        self,
        url: str,
        *,
        params: QueryParamsType = None,
    ) -> None:
        """DELETE request (no response body expected)."""
        self.request("DELETE", url, params=params)

    def upload_multipart(
        self,
        url: str,
        metadata: dict[str, Any],
        content: bytes,
        content_type: str,
        *,
        params: QueryParamsType = None,
    ) -> dict[str, Any]:
        """Upload file using multipart/related encoding (Drive API).

        Encodes JSON metadata + file content as multipart/related body.
        Replaces googleapiclient's MediaFileUpload/MediaInMemoryUpload.

        Args:
            url: Upload endpoint (e.g. Drive upload API)
            metadata: JSON metadata (name, mimeType, parents, etc.)
            content: Raw file bytes
            content_type: MIME type of the file content
            params: Optional query parameters (uploadType, fields, etc.)
        """
        import uuid
        boundary = f"mise_{uuid.uuid4().hex}"
        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        ).encode()
        body += orjson.dumps(metadata) + b"\r\n"
        body += (
            f"--{boundary}\r\n"
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
        body += content + f"\r\n--{boundary}--".encode()

        response = self.request(
            "POST", url,
            content=body,
            content_type=f"multipart/related; boundary={boundary}",
            params=params,
        )
        return orjson.loads(response.content)

    def stream_to_file(
        self,
        url: str,
        file_obj: Any,
        *,
        params: QueryParamsType = None,
        chunk_size: int = 65536,
    ) -> None:
        """Stream a download directly to a file object.

        Args:
            url: URL to download
            file_obj: File-like object to write to (must be opened in binary mode)
            params: Optional query parameters
            chunk_size: Download chunk size in bytes (default: 64KB)
        """
        req_headers = self._auth_headers()
        with self._client.stream(
            "GET", url, headers=req_headers, params=params,
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(chunk_size=chunk_size):
                file_obj.write(chunk)

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
