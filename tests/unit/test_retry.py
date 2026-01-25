"""
Tests for retry decorator and helper functions.

Tests cover:
- _get_http_status: HTTP status extraction from various exception types
- _should_retry: Determining if an exception should trigger retry
- _convert_to_mise_error: Converting exceptions to MiseError
- _calculate_wait_with_jitter: Backoff calculation
- with_retry decorator: Both sync and async functions
"""

import asyncio
import pytest
from unittest.mock import Mock, patch, MagicMock

from retry import (
    _get_http_status,
    _should_retry,
    _convert_to_mise_error,
    _calculate_wait_with_jitter,
    with_retry,
    RETRYABLE_STATUS_CODES,
)
from models import MiseError, ErrorKind


class TestGetHttpStatus:
    """Tests for _get_http_status function."""

    def test_googleapiclient_style_exception(self) -> None:
        """Extract status from googleapiclient HttpError style."""
        exc = Exception("API Error")
        exc.resp = Mock()
        exc.resp.status = 404
        assert _get_http_status(exc) == 404

    def test_requests_style_exception(self) -> None:
        """Extract status from requests-style exception."""
        exc = Exception("Request failed")
        exc.status_code = 500
        assert _get_http_status(exc) == 500

    def test_no_status_returns_none(self) -> None:
        """Plain exception without status returns None."""
        exc = Exception("Generic error")
        assert _get_http_status(exc) is None

    def test_non_int_status_ignored(self) -> None:
        """Non-integer status is ignored."""
        exc = Exception("Error")
        exc.resp = Mock()
        exc.resp.status = "not_a_number"
        assert _get_http_status(exc) is None

    def test_prefers_resp_status_over_status_code(self) -> None:
        """When both exist, resp.status is checked first."""
        exc = Exception("Error")
        exc.resp = Mock()
        exc.resp.status = 403
        exc.status_code = 500
        # Should return 403 (resp.status checked first)
        assert _get_http_status(exc) == 403


class TestShouldRetry:
    """Tests for _should_retry function."""

    def test_connection_error_is_retryable(self) -> None:
        """ConnectionError should trigger retry."""
        assert _should_retry(ConnectionError("Connection refused"))

    def test_timeout_error_is_retryable(self) -> None:
        """TimeoutError should trigger retry."""
        assert _should_retry(TimeoutError("Request timed out"))

    def test_rate_limited_is_retryable(self) -> None:
        """HTTP 429 should trigger retry."""
        exc = Exception("Rate limited")
        exc.resp = Mock(status=429)
        assert _should_retry(exc)

    def test_server_errors_are_retryable(self) -> None:
        """HTTP 5xx should trigger retry."""
        for status in [500, 502, 503, 504]:
            exc = Exception(f"Server error {status}")
            exc.resp = Mock(status=status)
            assert _should_retry(exc), f"HTTP {status} should be retryable"

    def test_not_found_is_not_retryable(self) -> None:
        """HTTP 404 should not trigger retry."""
        exc = Exception("Not found")
        exc.resp = Mock(status=404)
        assert not _should_retry(exc)

    def test_permission_denied_is_not_retryable(self) -> None:
        """HTTP 403 should not trigger retry."""
        exc = Exception("Forbidden")
        exc.resp = Mock(status=403)
        assert not _should_retry(exc)

    def test_auth_error_is_not_retryable(self) -> None:
        """HTTP 401 should not trigger retry."""
        exc = Exception("Unauthorized")
        exc.resp = Mock(status=401)
        assert not _should_retry(exc)

    def test_generic_exception_is_not_retryable(self) -> None:
        """Unknown exceptions without HTTP status are not retryable."""
        assert not _should_retry(ValueError("Invalid input"))

    def test_all_retryable_status_codes_covered(self) -> None:
        """Verify all documented retryable codes."""
        expected = {429, 500, 502, 503, 504}
        assert RETRYABLE_STATUS_CODES == expected


class TestConvertToMiseError:
    """Tests for _convert_to_mise_error function."""

    def test_mise_error_passes_through(self) -> None:
        """Existing MiseError is returned unchanged."""
        original = MiseError(ErrorKind.NOT_FOUND, "File missing")
        result = _convert_to_mise_error(original)
        assert result is original

    def test_401_becomes_auth_expired(self) -> None:
        """HTTP 401 converts to AUTH_EXPIRED."""
        exc = Exception("Unauthorized")
        exc.resp = Mock(status=401)
        result = _convert_to_mise_error(exc)
        assert result.kind == ErrorKind.AUTH_EXPIRED

    def test_403_becomes_permission_denied(self) -> None:
        """HTTP 403 converts to PERMISSION_DENIED."""
        exc = Exception("Forbidden")
        exc.resp = Mock(status=403)
        result = _convert_to_mise_error(exc)
        assert result.kind == ErrorKind.PERMISSION_DENIED

    def test_404_becomes_not_found(self) -> None:
        """HTTP 404 converts to NOT_FOUND."""
        exc = Exception("Not found")
        exc.resp = Mock(status=404)
        result = _convert_to_mise_error(exc)
        assert result.kind == ErrorKind.NOT_FOUND

    def test_429_becomes_rate_limited(self) -> None:
        """HTTP 429 converts to RATE_LIMITED with retryable flag."""
        exc = Exception("Rate limited")
        exc.resp = Mock(status=429)
        result = _convert_to_mise_error(exc)
        assert result.kind == ErrorKind.RATE_LIMITED
        assert result.retryable

    def test_5xx_becomes_network_error(self) -> None:
        """HTTP 5xx converts to NETWORK_ERROR with retryable flag."""
        for status in [500, 502, 503, 504]:
            exc = Exception(f"Server error")
            exc.resp = Mock(status=status)
            result = _convert_to_mise_error(exc)
            assert result.kind == ErrorKind.NETWORK_ERROR
            assert result.retryable

    def test_connection_error_becomes_network_error(self) -> None:
        """ConnectionError converts to NETWORK_ERROR."""
        result = _convert_to_mise_error(ConnectionError("Connection refused"))
        assert result.kind == ErrorKind.NETWORK_ERROR
        assert result.retryable

    def test_timeout_error_becomes_network_error(self) -> None:
        """TimeoutError converts to NETWORK_ERROR."""
        result = _convert_to_mise_error(TimeoutError("Timed out"))
        assert result.kind == ErrorKind.NETWORK_ERROR
        assert result.retryable

    def test_unknown_exception_becomes_unknown(self) -> None:
        """Unknown exceptions convert to UNKNOWN kind."""
        result = _convert_to_mise_error(ValueError("Bad value"))
        assert result.kind == ErrorKind.UNKNOWN

    def test_message_preserved(self) -> None:
        """Exception message is preserved in MiseError."""
        result = _convert_to_mise_error(ValueError("Important message"))
        assert "Important message" in result.message

    @patch("retry.clear_service_cache")
    def test_401_clears_service_cache(self, mock_clear: MagicMock) -> None:
        """HTTP 401 should clear cached services."""
        exc = Exception("Unauthorized")
        exc.resp = Mock(status=401)
        _convert_to_mise_error(exc)
        mock_clear.assert_called_once()


class TestCalculateWaitWithJitter:
    """Tests for _calculate_wait_with_jitter function."""

    def test_first_attempt_uses_base_delay(self) -> None:
        """First attempt (0) should use approximately base delay."""
        # With jitter, result should be within ±25% of 1000
        results = [
            _calculate_wait_with_jitter(1000, 0, 2.0, 0.25)
            for _ in range(100)
        ]
        avg = sum(results) / len(results)
        # Average should be close to base (1000)
        assert 750 <= avg <= 1250

    def test_exponential_backoff(self) -> None:
        """Later attempts should use exponential backoff."""
        # With backoff_multiplier=2, attempt 2 should be ~4x base
        results = [
            _calculate_wait_with_jitter(1000, 2, 2.0, 0.0)  # No jitter
            for _ in range(10)
        ]
        # Should all be exactly 4000 with no jitter
        assert all(r == 4000 for r in results)

    def test_jitter_adds_variation(self) -> None:
        """Jitter should add variation to wait times."""
        results = [
            _calculate_wait_with_jitter(1000, 0, 2.0, 0.25)
            for _ in range(100)
        ]
        # Should have variation (not all same value)
        assert len(set(results)) > 1

    def test_never_returns_negative(self) -> None:
        """Wait time should never be negative."""
        results = [
            _calculate_wait_with_jitter(100, 0, 2.0, 0.5)  # Large jitter
            for _ in range(1000)
        ]
        assert all(r >= 0 for r in results)


class TestWithRetrySync:
    """Tests for with_retry decorator with sync functions."""

    def test_successful_call_returns_result(self) -> None:
        """Successful call returns result without retry."""
        @with_retry(max_attempts=3, delay_ms=1)
        def succeed() -> str:
            return "success"

        assert succeed() == "success"

    def test_retries_on_retryable_error(self) -> None:
        """Function is retried on retryable errors."""
        attempts = [0]

        @with_retry(max_attempts=3, delay_ms=1)
        def fail_then_succeed() -> str:
            attempts[0] += 1
            if attempts[0] < 3:
                raise ConnectionError("Temporary failure")
            return "success"

        result = fail_then_succeed()
        assert result == "success"
        assert attempts[0] == 3

    def test_raises_on_non_retryable_error(self) -> None:
        """Non-retryable errors are raised immediately."""
        attempts = [0]

        @with_retry(max_attempts=3, delay_ms=1)
        def fail_not_found() -> str:
            attempts[0] += 1
            exc = Exception("Not found")
            exc.resp = Mock(status=404)
            raise exc

        with pytest.raises(MiseError) as exc_info:
            fail_not_found()

        assert exc_info.value.kind == ErrorKind.NOT_FOUND
        assert attempts[0] == 1  # No retries

    def test_raises_after_max_attempts(self) -> None:
        """Raises after exhausting max attempts."""
        attempts = [0]

        @with_retry(max_attempts=3, delay_ms=1)
        def always_fail() -> str:
            attempts[0] += 1
            raise ConnectionError("Always fails")

        with pytest.raises(MiseError) as exc_info:
            always_fail()

        assert exc_info.value.kind == ErrorKind.NETWORK_ERROR
        assert attempts[0] == 3

    def test_convert_errors_false_preserves_original(self) -> None:
        """With convert_errors=False, original exception is raised."""
        @with_retry(max_attempts=3, delay_ms=1, convert_errors=False)
        def fail_immediately() -> str:
            exc = Exception("Not found")
            exc.resp = Mock(status=404)
            raise exc

        with pytest.raises(Exception) as exc_info:
            fail_immediately()

        # Should be original exception, not MiseError
        assert not isinstance(exc_info.value, MiseError)
        assert "Not found" in str(exc_info.value)


class TestWithRetryAsync:
    """Tests for with_retry decorator with async functions."""

    @pytest.mark.asyncio
    async def test_successful_async_call(self) -> None:
        """Successful async call returns result."""
        @with_retry(max_attempts=3, delay_ms=1)
        async def async_succeed() -> str:
            return "async success"

        result = await async_succeed()
        assert result == "async success"

    @pytest.mark.asyncio
    async def test_retries_async_on_retryable_error(self) -> None:
        """Async function is retried on retryable errors."""
        attempts = [0]

        @with_retry(max_attempts=3, delay_ms=1)
        async def async_fail_then_succeed() -> str:
            attempts[0] += 1
            if attempts[0] < 3:
                raise TimeoutError("Temporary failure")
            return "success"

        result = await async_fail_then_succeed()
        assert result == "success"
        assert attempts[0] == 3

    @pytest.mark.asyncio
    async def test_async_raises_on_non_retryable(self) -> None:
        """Async non-retryable errors are raised immediately."""
        @with_retry(max_attempts=3, delay_ms=1)
        async def async_fail_forbidden() -> str:
            exc = Exception("Forbidden")
            exc.resp = Mock(status=403)
            raise exc

        with pytest.raises(MiseError) as exc_info:
            await async_fail_forbidden()

        assert exc_info.value.kind == ErrorKind.PERMISSION_DENIED

    @pytest.mark.asyncio
    async def test_async_raises_after_max_attempts(self) -> None:
        """Async raises after exhausting max attempts."""
        attempts = [0]

        @with_retry(max_attempts=2, delay_ms=1)
        async def async_always_fail() -> str:
            attempts[0] += 1
            raise ConnectionError("Always fails")

        with pytest.raises(MiseError):
            await async_always_fail()

        assert attempts[0] == 2


class TestDecoratorDetection:
    """Tests for correct sync/async detection."""

    def test_detects_sync_function(self) -> None:
        """Decorator correctly identifies sync function."""
        @with_retry(max_attempts=1, delay_ms=1)
        def sync_func() -> str:
            return "sync"

        # Should work without await
        result = sync_func()
        assert result == "sync"

    @pytest.mark.asyncio
    async def test_detects_async_function(self) -> None:
        """Decorator correctly identifies async function."""
        @with_retry(max_attempts=1, delay_ms=1)
        async def async_func() -> str:
            return "async"

        # Should require await
        result = await async_func()
        assert result == "async"
