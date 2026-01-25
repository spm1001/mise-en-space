"""
Mock utilities for adapter testing.

Provides helpers for creating mock Google API services and errors.
"""

from googleapiclient.errors import HttpError
from httplib2 import Response


def make_http_error(status: int, message: str = "Error") -> HttpError:
    """
    Create an HttpError for testing error handling.

    Args:
        status: HTTP status code (403, 404, 500, etc.)
        message: Error message

    Returns:
        HttpError that can be raised in mock side_effect

    Example:
        mock_service.files().get().execute.side_effect = make_http_error(404, "Not found")
    """
    resp = Response({"status": status})
    return HttpError(resp, message.encode())
