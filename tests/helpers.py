"""
Shared test helpers for mise-en-space.

Centralizes mock wiring patterns that repeat across test files.
"""

from unittest.mock import MagicMock
from typing import Any


def mock_api_chain(
    mock_service: MagicMock,
    chain: str,
    response: Any = None,
    *,
    side_effect: Any = None,
) -> MagicMock:
    """Set up a mock Google API response for a chained call.

    Navigates the MagicMock attribute chain and sets return_value (or side_effect)
    on the final method. Returns the final mock method for adding assertions.

    Args:
        mock_service: The mocked service object (from @patch)
        chain: Dot-separated chain. Each part except the last is treated as
               a callable method (traversed via .return_value).
               Examples: "files.get.execute", "users.threads.get.execute",
                         "spreadsheets.values.batchGet.execute"
        response: The return value for the final method
        side_effect: Alternative to response — sets side_effect instead

    Returns:
        The final mock method (for adding assertions like assert_called_once_with)

    Examples:
        # Simple:
        mock_api_chain(service, "files.get.execute", {"id": "f1"})
        # equivalent to: service.files().get().execute.return_value = {"id": "f1"}

        # With assertion:
        execute = mock_api_chain(service, "files.list.execute", {"files": []})
        # ... call adapter ...
        execute.assert_called_once()

        # With side_effect:
        mock_api_chain(service, "files.get.execute", side_effect=HttpError(...))
    """
    parts = chain.split(".")
    obj = mock_service
    for part in parts[:-1]:
        obj = getattr(obj, part).return_value
    final = getattr(obj, parts[-1])
    if side_effect is not None:
        final.side_effect = side_effect
    elif response is not None:
        final.return_value = response
    return final
