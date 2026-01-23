"""
Retry decorator with exponential backoff.

Used by adapters to handle transient API failures.
"""

import asyncio
import time
from functools import wraps
from typing import TypeVar, Callable, Any, ParamSpec, Awaitable, cast

from logging_config import logger, log_retry
from models import MiseError, ErrorKind

T = TypeVar("T")
P = ParamSpec("P")


# Exceptions that should trigger retry
RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
)

# HTTP status codes that should trigger retry
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({
    429,  # Rate limited
    500,  # Internal server error
    502,  # Bad gateway
    503,  # Service unavailable
    504,  # Gateway timeout
})


def _get_http_status(exception: Exception) -> int | None:
    """
    Extract HTTP status code from exception if available.

    Works with googleapiclient.errors.HttpError and similar.
    """
    # Check for resp.status attribute (googleapiclient.errors.HttpError)
    if hasattr(exception, "resp") and hasattr(exception.resp, "status"):
        status = exception.resp.status
        if isinstance(status, int):
            return status

    # Check for status_code attribute (requests-style)
    if hasattr(exception, "status_code"):
        status = exception.status_code
        if isinstance(status, int):
            return status

    return None


def _should_retry(exception: Exception) -> bool:
    """Determine if an exception is retryable."""
    # Check if it's a known retryable exception type
    if isinstance(exception, RETRYABLE_EXCEPTIONS):
        return True

    # Check for HTTP status code
    status = _get_http_status(exception)
    if status is not None and status in RETRYABLE_STATUS_CODES:
        return True

    return False


def _convert_to_mise_error(exception: Exception) -> MiseError:
    """Convert an exception to a MiseError if not already one."""
    if isinstance(exception, MiseError):
        return exception

    # Check HTTP status first (more reliable than string matching)
    status = _get_http_status(exception)
    if status is not None:
        if status == 401:
            return MiseError(ErrorKind.AUTH_EXPIRED, str(exception))
        elif status == 403:
            return MiseError(ErrorKind.PERMISSION_DENIED, str(exception))
        elif status == 404:
            return MiseError(ErrorKind.NOT_FOUND, str(exception))
        elif status == 429:
            return MiseError(ErrorKind.RATE_LIMITED, str(exception), retryable=True)
        elif status >= 500:
            return MiseError(ErrorKind.NETWORK_ERROR, str(exception), retryable=True)

    # Fall back to exception type
    if isinstance(exception, (ConnectionError, TimeoutError)):
        return MiseError(ErrorKind.NETWORK_ERROR, str(exception), retryable=True)

    return MiseError(ErrorKind.UNKNOWN, str(exception))


def with_retry(
    max_attempts: int = 3,
    delay_ms: int = 1000,
    backoff_multiplier: float = 2.0,
    convert_errors: bool = True,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Retry decorator with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        delay_ms: Initial delay in milliseconds
        backoff_multiplier: Multiplier for exponential backoff
        convert_errors: Convert exceptions to MiseError on final failure

    Returns:
        Decorated function with retry logic

    Example:
        @with_retry(max_attempts=3, delay_ms=1000)
        def fetch_file(file_id: str):
            return service.files().get(fileId=file_id).execute()
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Exception | None = None

            for attempt in range(max_attempts):
                try:
                    # Cast needed because mypy can't verify async at runtime
                    coro = cast(Awaitable[T], func(*args, **kwargs))
                    return await coro
                except Exception as e:
                    last_exception = e

                    # Check if we should retry
                    if not _should_retry(e) or attempt == max_attempts - 1:
                        logger.error(
                            f"{func.__name__} failed after {attempt + 1} attempts: {e}"
                        )
                        if convert_errors:
                            raise _convert_to_mise_error(e) from e
                        raise

                    # Calculate wait time with exponential backoff
                    wait_ms = int(delay_ms * (backoff_multiplier ** attempt))
                    log_retry(attempt + 1, max_attempts, wait_ms, str(e))
                    await asyncio.sleep(wait_ms / 1000)

            # Should never reach here, but satisfy type checker
            assert last_exception is not None
            if convert_errors:
                raise _convert_to_mise_error(last_exception) from last_exception
            raise last_exception

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Exception | None = None

            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e

                    if not _should_retry(e) or attempt == max_attempts - 1:
                        logger.error(
                            f"{func.__name__} failed after {attempt + 1} attempts: {e}"
                        )
                        if convert_errors:
                            raise _convert_to_mise_error(e) from e
                        raise

                    wait_ms = int(delay_ms * (backoff_multiplier ** attempt))
                    log_retry(attempt + 1, max_attempts, wait_ms, str(e))
                    time.sleep(wait_ms / 1000)

            # Should never reach here, but satisfy type checker
            assert last_exception is not None
            if convert_errors:
                raise _convert_to_mise_error(last_exception) from last_exception
            raise last_exception

        # Return appropriate wrapper based on whether function is async
        if asyncio.iscoroutinefunction(func):
            return cast(Callable[P, T], async_wrapper)
        else:
            return cast(Callable[P, T], sync_wrapper)

    return decorator
