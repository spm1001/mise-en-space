"""
Forms adapter — Google Forms API v1 wrapper.

Fetches form structure (questions, sections, options) via Forms REST API.
"""

from typing import Any

from retry import with_retry
from adapters.http_client import get_sync_client


_FORMS_API = "https://forms.googleapis.com/v1/forms"


@with_retry(max_attempts=3, delay_ms=1000)
def fetch_form(form_id: str) -> dict[str, Any]:
    """Fetch form structure from the Forms API."""
    client = get_sync_client()
    return client.get_json(f"{_FORMS_API}/{form_id}")
