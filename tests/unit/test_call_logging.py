"""Tests for MCP call logging (JSONL file handler + log_mcp_call)."""

import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from logging_config import (
    _calls_logger,
    configure_call_logging,
    log_mcp_call,
)


@pytest.fixture(autouse=True)
def _isolate_calls_logger():
    """Remove any handlers added during tests so they don't leak."""
    original_handlers = list(_calls_logger.handlers)
    yield
    _calls_logger.handlers = original_handlers


class TestConfigureCallLogging:
    """configure_call_logging() wires a RotatingFileHandler."""

    def test_creates_log_file_and_returns_path(self, tmp_path: Path) -> None:
        log_file = tmp_path / "calls.jsonl"
        with patch("logging_config._CALLS_DIR", tmp_path), \
             patch("logging_config._CALLS_FILE", log_file):
            result = configure_call_logging()

        assert result == log_file
        assert any(
            isinstance(h, logging.handlers.RotatingFileHandler)
            for h in _calls_logger.handlers
        )

    def test_creates_directory_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "path"
        log_file = nested / "calls.jsonl"
        with patch("logging_config._CALLS_DIR", nested), \
             patch("logging_config._CALLS_FILE", log_file):
            result = configure_call_logging()

        assert result == log_file
        assert nested.exists()

    def test_returns_none_if_dir_creation_fails(self, tmp_path: Path) -> None:
        with patch("logging_config._CALLS_DIR", tmp_path), \
             patch("logging_config._CALLS_FILE", tmp_path / "calls.jsonl"), \
             patch("pathlib.Path.mkdir", side_effect=OSError("permission denied")):
            result = configure_call_logging()

        assert result is None

    def test_idempotent(self, tmp_path: Path) -> None:
        log_file = tmp_path / "calls.jsonl"
        with patch("logging_config._CALLS_DIR", tmp_path), \
             patch("logging_config._CALLS_FILE", log_file):
            configure_call_logging()
            handler_count = len(_calls_logger.handlers)
            configure_call_logging()  # second call
            assert len(_calls_logger.handlers) == handler_count


class TestLogMcpCall:
    """log_mcp_call() writes structured JSONL records."""

    @pytest.fixture()
    def log_file(self, tmp_path: Path) -> Path:
        """Wire a real file handler for the test."""
        f = tmp_path / "calls.jsonl"
        with patch("logging_config._CALLS_DIR", tmp_path), \
             patch("logging_config._CALLS_FILE", f):
            configure_call_logging()
        return f

    def _read_records(self, log_file: Path) -> list[dict]:
        # Flush handlers to ensure writes are on disk
        for h in _calls_logger.handlers:
            h.flush()
        lines = log_file.read_text().strip().splitlines()
        return [json.loads(line) for line in lines]

    def test_writes_search_call(self, log_file: Path) -> None:
        log_mcp_call("search", params={"query": "budget", "sources": ["drive"]})
        records = self._read_records(log_file)
        assert len(records) == 1
        rec = records[0]
        assert rec["tool"] == "search"
        assert rec["params"]["query"] == "budget"
        assert "ts" in rec
        assert "ok" not in rec  # ok=True is omitted for brevity

    def test_writes_do_call_with_result(self, log_file: Path) -> None:
        log_mcp_call(
            "do",
            params={"operation": "create", "title": "TitleName", "content_len": 42},
            result_summary={"file_id": "abc123", "title": "TitleName"},
        )
        records = self._read_records(log_file)
        rec = records[0]
        assert rec["tool"] == "do"
        assert rec["params"]["title"] == "TitleName"
        assert rec["result"]["file_id"] == "abc123"

    def test_writes_error_call(self, log_file: Path) -> None:
        log_mcp_call(
            "fetch",
            params={"file_id": "bad123"},
            ok=False,
            error="NOT_FOUND: file not accessible",
        )
        records = self._read_records(log_file)
        rec = records[0]
        assert rec["ok"] is False
        assert "NOT_FOUND" in rec["error"]

    def test_timestamp_is_iso_format(self, log_file: Path) -> None:
        log_mcp_call("search", params={"query": "test"})
        records = self._read_records(log_file)
        ts = records[0]["ts"]
        # Should be YYYY-MM-DDTHH:MM:SSZ
        assert ts.endswith("Z")
        assert len(ts) == 20

    def test_multiple_calls_produce_multiple_lines(self, log_file: Path) -> None:
        log_mcp_call("search", params={"query": "a"})
        log_mcp_call("fetch", params={"file_id": "x"})
        log_mcp_call("do", params={"operation": "create"})
        records = self._read_records(log_file)
        assert len(records) == 3
        assert [r["tool"] for r in records] == ["search", "fetch", "do"]

    def test_no_handler_is_silent(self) -> None:
        """log_mcp_call doesn't crash when no handler is configured."""
        # _calls_logger has no handlers after autouse fixture restores original
        # (which was empty before any test configured one)
        saved = list(_calls_logger.handlers)
        _calls_logger.handlers = []
        try:
            log_mcp_call("search", params={"query": "test"})  # should not raise
        finally:
            _calls_logger.handlers = saved


class TestServerIntegration:
    """server.py tool functions call log_mcp_call."""

    @patch("server.log_mcp_call")
    @patch("server.do_search")
    def test_search_logs_call(self, mock_search: MagicMock, mock_log: MagicMock) -> None:
        from server import search
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"drive_count": 5, "gmail_count": 2}
        mock_search.return_value = mock_result

        search(query="budget", base_path="/tmp/test")

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args.kwargs["params"]["query"] == "budget"

    @patch("server.log_mcp_call")
    @patch("server.do_fetch")
    def test_fetch_logs_call(self, mock_fetch: MagicMock, mock_log: MagicMock) -> None:
        from server import fetch
        mock_result = MagicMock(spec=["to_dict"])
        mock_result.to_dict.return_value = {"type": "doc", "format": "markdown", "metadata": {"title": "Test"}}
        mock_fetch.return_value = mock_result

        fetch(file_id="abc123", base_path="/tmp/test")

        mock_log.assert_called_once()
        assert mock_log.call_args.kwargs["params"]["file_id"] == "abc123"

    @patch("server.log_mcp_call")
    def test_do_unknown_op_logs_error(self, mock_log: MagicMock) -> None:
        from server import do
        do(operation="explode")

        mock_log.assert_called_once()
        assert mock_log.call_args.kwargs["ok"] is False
        assert "explode" in mock_log.call_args.kwargs["error"]

    @patch("server.log_mcp_call")
    def test_do_missing_params_logs_error(self, mock_log: MagicMock) -> None:
        from server import do
        do(operation="move")

        mock_log.assert_called_once()
        assert mock_log.call_args.kwargs["ok"] is False

    @patch("server.log_mcp_call")
    @patch("server.do_create")
    def test_do_create_logs_title(self, mock_create: MagicMock, mock_log: MagicMock) -> None:
        from server import do
        mock_create.return_value = MagicMock(
            to_dict=MagicMock(return_value={
                "file_id": "new123", "title": "TitleName",
                "web_link": "https://docs.google.com/...", "operation": "create",
            })
        )

        do(operation="create", content="# Hello", title="TitleName")

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args.kwargs
        assert call_kwargs["params"]["title"] == "TitleName"
        assert call_kwargs["params"]["content_len"] == 7
        assert call_kwargs["result_summary"]["file_id"] == "new123"
