"""
Logging configuration for mise-en-space.

Simple setup that adapters and tools can import.
Extractors should NOT log (they're pure functions).

Call logging: configure_call_logging() wires a JSONL RotatingFileHandler
to ~/.local/share/mise/calls.jsonl. log_mcp_call() writes structured
records for every search/fetch/do invocation.
"""

import json
import logging
import logging.handlers
import sys
import time
from pathlib import Path
from typing import Any

# Create logger for the package
logger = logging.getLogger("mise")

# Dedicated logger for MCP call records (JSONL file only, no stderr)
_calls_logger = logging.getLogger("mise.calls")
_calls_logger.propagate = False

_CALLS_DIR = Path.home() / ".local" / "share" / "mise"
_CALLS_FILE = _CALLS_DIR / "calls.jsonl"


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


def configure_call_logging() -> Path | None:
    """Wire JSONL file handler for MCP call logging.

    Creates ~/.local/share/mise/calls.jsonl with 5MB rotation (3 backups).
    Returns the log file path, or None if the directory can't be created.
    Call once at server startup (server.py __main__).
    """
    if _calls_logger.handlers:
        return _CALLS_FILE  # Already configured

    try:
        _CALLS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    handler = logging.handlers.RotatingFileHandler(
        _CALLS_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    # Raw message only — log_mcp_call() pre-formats as JSON
    handler.setFormatter(logging.Formatter("%(message)s"))
    _calls_logger.addHandler(handler)
    _calls_logger.setLevel(logging.INFO)
    return _CALLS_FILE


def log_mcp_call(
    tool: str,
    *,
    params: dict[str, Any] | None = None,
    ok: bool = True,
    error: str | None = None,
    result_summary: dict[str, Any] | None = None,
) -> None:
    """Write a structured JSONL record for an MCP tool call.

    Args:
        tool: "search", "fetch", or "do"
        params: Meaningful parameters (pre-filtered by caller)
        ok: Whether the call succeeded
        error: Error message if not ok
        result_summary: Key fields from the result (file_id, counts, etc.)
    """
    record: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool": tool,
    }
    if params:
        record["params"] = params
    if not ok:
        record["ok"] = False
        if error:
            record["error"] = error
    if result_summary:
        record["result"] = result_summary
    _calls_logger.info(json.dumps(record, default=str))


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
