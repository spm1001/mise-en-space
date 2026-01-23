"""
Logging configuration for mise-en-space.

Simple setup that adapters and tools can import.
Extractors should NOT log (they're pure functions).
"""

import logging
import sys

# Create logger for the package
logger = logging.getLogger("mise")


def configure_logging(level: str = "INFO") -> None:
    """
    Configure logging for mise-en-space.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
    """
    logger.setLevel(getattr(logging, level.upper()))

    # Only add handler if not already configured
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.DEBUG)

        # Concise format for MCP context
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)


# NOTE: Call configure_logging() explicitly in server.py or test setup.
# We don't auto-configure to avoid side effects on import.


# Convenience functions for common patterns
def log_api_call(service: str, method: str, **params: object) -> None:
    """Log an API call with key parameters."""
    param_str = ", ".join(f"{k}={v!r}" for k, v in params.items() if v is not None)
    logger.debug(f"API: {service}.{method}({param_str})")


def log_api_result(service: str, method: str, result_count: int | None = None) -> None:
    """Log API result summary."""
    if result_count is not None:
        logger.debug(f"API: {service}.{method} returned {result_count} results")
    else:
        logger.debug(f"API: {service}.{method} completed")


def log_retry(attempt: int, max_attempts: int, delay_ms: int, reason: str) -> None:
    """Log a retry attempt."""
    logger.warning(
        f"Retry {attempt}/{max_attempts} in {delay_ms}ms: {reason}"
    )
