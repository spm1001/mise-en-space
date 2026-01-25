"""Unit tests for slides adapter — mocked API calls."""

import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from tests.mock_utils import make_http_error
from models import PresentationData, SlideData


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
        self, mock_slides_service: MagicMock, sample_presentation_data: PresentationData
    ) -> None:
        """Test that HTTP 403 errors produce clear warning message."""
        from adapters.slides import _fetch_thumbnails_selective

        # Mock getThumbnail to raise 403 for first slide, succeed for second
        def get_thumbnail_side_effect(presentationId, pageObjectId, **kwargs):
            mock_request = MagicMock()
            if pageObjectId == "slide1":
                raise make_http_error(403, "Permission denied")
            else:
                mock_request.execute.return_value = {"contentUrl": "http://example.com/thumb.png"}
            return mock_request

        mock_slides_service.presentations().pages().getThumbnail.side_effect = get_thumbnail_side_effect

        with patch("adapters.slides.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b"fake-png-data"
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            _fetch_thumbnails_selective(mock_slides_service, "test-id", sample_presentation_data)

        # Check slide1 got the permission denied warning
        slide1 = sample_presentation_data.slides[0]
        assert len(slide1.warnings) == 1
        assert "permission denied" in slide1.warnings[0].lower()

        # Check slide2 got its thumbnail
        slide2 = sample_presentation_data.slides[1]
        assert slide2.thumbnail_bytes == b"fake-png-data"
        assert len(slide2.warnings) == 0

    def test_http_404_not_found(
        self, mock_slides_service: MagicMock, sample_presentation_data: PresentationData
    ) -> None:
        """Test that HTTP 404 errors produce clear warning message."""
        from adapters.slides import _fetch_thumbnails_selective

        mock_slides_service.presentations().pages().getThumbnail().execute.side_effect = (
            make_http_error(404, "Not found")
        )

        _fetch_thumbnails_selective(mock_slides_service, "test-id", sample_presentation_data)

        slide1 = sample_presentation_data.slides[0]
        assert len(slide1.warnings) == 1
        assert "not found" in slide1.warnings[0].lower()

    def test_download_timeout(
        self, mock_slides_service: MagicMock, sample_presentation_data: PresentationData
    ) -> None:
        """Test that download timeouts produce clear warning message."""
        from adapters.slides import _fetch_thumbnails_selective

        mock_slides_service.presentations().pages().getThumbnail().execute.return_value = {
            "contentUrl": "http://example.com/thumb.png"
        }

        with patch("adapters.slides.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError()
            _fetch_thumbnails_selective(mock_slides_service, "test-id", sample_presentation_data)

        slide1 = sample_presentation_data.slides[0]
        assert len(slide1.warnings) == 1
        assert "timeout" in slide1.warnings[0].lower()
        assert slide1.thumbnail_bytes is None

    def test_download_url_error(
        self, mock_slides_service: MagicMock, sample_presentation_data: PresentationData
    ) -> None:
        """Test that download URL errors produce clear warning message."""
        from adapters.slides import _fetch_thumbnails_selective

        mock_slides_service.presentations().pages().getThumbnail().execute.return_value = {
            "contentUrl": "http://example.com/thumb.png"
        }

        with patch("adapters.slides.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
            _fetch_thumbnails_selective(mock_slides_service, "test-id", sample_presentation_data)

        slide1 = sample_presentation_data.slides[0]
        assert len(slide1.warnings) == 1
        assert "download failed" in slide1.warnings[0].lower()
        assert "connection refused" in slide1.warnings[0].lower()

    def test_text_only_slides_skipped(
        self, mock_slides_service: MagicMock, sample_presentation_data: PresentationData
    ) -> None:
        """Test that slides with needs_thumbnail=False are not fetched."""
        from adapters.slides import _fetch_thumbnails_selective

        call_count = 0

        def count_calls(**kwargs):
            nonlocal call_count
            call_count += 1
            mock_request = MagicMock()
            mock_request.execute.return_value = {"contentUrl": "http://example.com/thumb.png"}
            return mock_request

        mock_slides_service.presentations().pages().getThumbnail = count_calls

        with patch("adapters.slides.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b"fake-png-data"
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            _fetch_thumbnails_selective(mock_slides_service, "test-id", sample_presentation_data)

        # Only 2 slides need thumbnails, slide3 is text_only
        assert call_count == 2

    def test_thumbnails_included_flag_set(
        self, mock_slides_service: MagicMock, sample_presentation_data: PresentationData
    ) -> None:
        """Test that thumbnails_included is set when at least one succeeds."""
        from adapters.slides import _fetch_thumbnails_selective

        mock_slides_service.presentations().pages().getThumbnail().execute.return_value = {
            "contentUrl": "http://example.com/thumb.png"
        }

        with patch("adapters.slides.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b"fake-png-data"
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            _fetch_thumbnails_selective(mock_slides_service, "test-id", sample_presentation_data)

        assert sample_presentation_data.thumbnails_included is True

    def test_thumbnails_included_false_when_all_fail(
        self, mock_slides_service: MagicMock, sample_presentation_data: PresentationData
    ) -> None:
        """Test that thumbnails_included is False when all fetches fail."""
        from adapters.slides import _fetch_thumbnails_selective

        mock_slides_service.presentations().pages().getThumbnail().execute.side_effect = (
            make_http_error(500, "Server error")
        )

        _fetch_thumbnails_selective(mock_slides_service, "test-id", sample_presentation_data)

        assert sample_presentation_data.thumbnails_included is False
