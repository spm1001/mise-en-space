"""Unit tests for slides adapter — mocked HTTP client."""

import urllib.error
from unittest.mock import MagicMock, patch

import httpx
import pytest

from models import PresentationData, SlideData


def _make_http_status_error(status: int) -> httpx.HTTPStatusError:
    """Create an httpx.HTTPStatusError for testing."""
    request = httpx.Request("GET", "https://slides.googleapis.com/test")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status}", request=request, response=response,
    )


class TestThumbnailFailureHandling:
    """Tests for thumbnail fetch error handling."""

    @pytest.fixture
    def sample_presentation_data(self) -> PresentationData:
        """Create presentation data with slides that need thumbnails."""
        return PresentationData(
            title="Test",
            presentation_id="test-id",
            slides=[
                SlideData(
                    slide_id="slide1",
                    index=0,
                    needs_thumbnail=True,
                    thumbnail_reason="chart",
                ),
                SlideData(
                    slide_id="slide2",
                    index=1,
                    needs_thumbnail=True,
                    thumbnail_reason="image",
                ),
                SlideData(
                    slide_id="slide3",
                    index=2,
                    needs_thumbnail=False,
                    skip_thumbnail_reason="text_only",
                ),
            ],
        )

    def test_http_403_permission_denied(
        self, sample_presentation_data: PresentationData
    ) -> None:
        """Test that HTTP 403 errors produce clear warning message."""
        from adapters.slides import _fetch_thumbnails_selective

        mock_client = MagicMock()

        def get_json_side_effect(url, **kwargs):
            if "slide1" in url:
                raise _make_http_status_error(403)
            return {"contentUrl": "http://example.com/thumb.png"}

        mock_client.get_json.side_effect = get_json_side_effect

        with patch("adapters.slides.get_sync_client", return_value=mock_client), \
             patch("adapters.slides.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b"fake-png-data"
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            _fetch_thumbnails_selective("test-id", sample_presentation_data)

        # Check slide1 got the permission denied warning
        slide1 = sample_presentation_data.slides[0]
        assert len(slide1.warnings) == 1
        assert "permission denied" in slide1.warnings[0].lower()

        # Check slide2 got its thumbnail
        slide2 = sample_presentation_data.slides[1]
        assert slide2.thumbnail_bytes == b"fake-png-data"
        assert len(slide2.warnings) == 0

    def test_http_404_not_found(
        self, sample_presentation_data: PresentationData
    ) -> None:
        """Test that HTTP 404 errors produce clear warning message."""
        from adapters.slides import _fetch_thumbnails_selective

        mock_client = MagicMock()
        mock_client.get_json.side_effect = _make_http_status_error(404)

        with patch("adapters.slides.get_sync_client", return_value=mock_client):
            _fetch_thumbnails_selective("test-id", sample_presentation_data)

        slide1 = sample_presentation_data.slides[0]
        assert len(slide1.warnings) == 1
        assert "not found" in slide1.warnings[0].lower()

    def test_download_timeout(
        self, sample_presentation_data: PresentationData
    ) -> None:
        """Test that download timeouts produce clear warning message."""
        from adapters.slides import _fetch_thumbnails_selective

        mock_client = MagicMock()
        mock_client.get_json.return_value = {
            "contentUrl": "http://example.com/thumb.png"
        }

        with patch("adapters.slides.get_sync_client", return_value=mock_client), \
             patch("adapters.slides.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError()
            _fetch_thumbnails_selective("test-id", sample_presentation_data)

        slide1 = sample_presentation_data.slides[0]
        assert len(slide1.warnings) == 1
        assert "timeout" in slide1.warnings[0].lower()
        assert slide1.thumbnail_bytes is None

    def test_download_url_error(
        self, sample_presentation_data: PresentationData
    ) -> None:
        """Test that download URL errors produce clear warning message."""
        from adapters.slides import _fetch_thumbnails_selective

        mock_client = MagicMock()
        mock_client.get_json.return_value = {
            "contentUrl": "http://example.com/thumb.png"
        }

        with patch("adapters.slides.get_sync_client", return_value=mock_client), \
             patch("adapters.slides.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
            _fetch_thumbnails_selective("test-id", sample_presentation_data)

        slide1 = sample_presentation_data.slides[0]
        assert len(slide1.warnings) == 1
        assert "download failed" in slide1.warnings[0].lower()
        assert "connection refused" in slide1.warnings[0].lower()

    def test_text_only_slides_skipped(
        self, sample_presentation_data: PresentationData
    ) -> None:
        """Test that slides with needs_thumbnail=False are not fetched."""
        from adapters.slides import _fetch_thumbnails_selective

        mock_client = MagicMock()
        mock_client.get_json.return_value = {
            "contentUrl": "http://example.com/thumb.png"
        }

        with patch("adapters.slides.get_sync_client", return_value=mock_client), \
             patch("adapters.slides.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b"fake-png-data"
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            _fetch_thumbnails_selective("test-id", sample_presentation_data)

        # Only 2 slides need thumbnails, slide3 is text_only
        assert mock_client.get_json.call_count == 2

    def test_thumbnails_included_flag_set(
        self, sample_presentation_data: PresentationData
    ) -> None:
        """Test that thumbnails_included is set when at least one succeeds."""
        from adapters.slides import _fetch_thumbnails_selective

        mock_client = MagicMock()
        mock_client.get_json.return_value = {
            "contentUrl": "http://example.com/thumb.png"
        }

        with patch("adapters.slides.get_sync_client", return_value=mock_client), \
             patch("adapters.slides.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b"fake-png-data"
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            _fetch_thumbnails_selective("test-id", sample_presentation_data)

        assert sample_presentation_data.thumbnails_included is True

    def test_thumbnails_included_false_when_all_fail(
        self, sample_presentation_data: PresentationData
    ) -> None:
        """Test that thumbnails_included is False when all fetches fail."""
        from adapters.slides import _fetch_thumbnails_selective

        mock_client = MagicMock()
        mock_client.get_json.side_effect = _make_http_status_error(500)

        with patch("adapters.slides.get_sync_client", return_value=mock_client):
            _fetch_thumbnails_selective("test-id", sample_presentation_data)

        assert sample_presentation_data.thumbnails_included is False
