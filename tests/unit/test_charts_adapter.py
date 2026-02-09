"""Unit tests for charts adapter — mocked API calls."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests  # type: ignore[import-untyped]

from models import ChartData
from adapters.charts import get_charts_from_spreadsheet, render_charts_as_pngs
from tests.helpers import mock_api_chain


class TestGetChartsFromSpreadsheet:
    """Tests for chart metadata parsing."""

    def test_parses_basic_chart(self) -> None:
        """Test parsing a basicChart with chartType."""
        response = {
            "sheets": [{
                "properties": {"title": "Data"},
                "charts": [{
                    "chartId": 123,
                    "spec": {
                        "title": "Sales by Region",
                        "basicChart": {"chartType": "COLUMN"}
                    }
                }]
            }]
        }

        charts = get_charts_from_spreadsheet(response)

        assert len(charts) == 1
        assert charts[0].chart_id == 123
        assert charts[0].title == "Sales by Region"
        assert charts[0].sheet_name == "Data"
        assert charts[0].chart_type == "COLUMN"

    def test_parses_pie_chart(self) -> None:
        """Test parsing a pieChart (different structure than basicChart)."""
        response = {
            "sheets": [{
                "properties": {"title": "Summary"},
                "charts": [{
                    "chartId": 456,
                    "spec": {
                        "title": "Distribution",
                        "pieChart": {"legendPosition": "RIGHT_LEGEND"}
                    }
                }]
            }]
        }

        charts = get_charts_from_spreadsheet(response)

        assert len(charts) == 1
        assert charts[0].chart_type == "PIE"

    def test_parses_histogram_chart(self) -> None:
        """Test parsing a histogramChart."""
        response = {
            "sheets": [{
                "properties": {"title": "Analysis"},
                "charts": [{
                    "chartId": 789,
                    "spec": {
                        "title": "Age Distribution",
                        "histogramChart": {"bucketSize": 5}
                    }
                }]
            }]
        }

        charts = get_charts_from_spreadsheet(response)

        assert len(charts) == 1
        assert charts[0].chart_type == "HISTOGRAM"

    def test_skips_chart_without_id(self) -> None:
        """Test that charts without chartId are skipped."""
        response = {
            "sheets": [{
                "properties": {"title": "Data"},
                "charts": [
                    {"chartId": 123, "spec": {"title": "Valid"}},
                    {"spec": {"title": "No ID"}},  # Missing chartId
                    {"chartId": 456, "spec": {"title": "Also Valid"}},
                ]
            }]
        }

        charts = get_charts_from_spreadsheet(response)

        assert len(charts) == 2
        assert charts[0].chart_id == 123
        assert charts[1].chart_id == 456

    def test_handles_multiple_sheets(self) -> None:
        """Test parsing charts from multiple sheets."""
        response = {
            "sheets": [
                {
                    "properties": {"title": "Sheet1"},
                    "charts": [{"chartId": 1, "spec": {"title": "Chart 1"}}]
                },
                {
                    "properties": {"title": "Sheet2"},
                    "charts": [{"chartId": 2, "spec": {"title": "Chart 2"}}]
                },
            ]
        }

        charts = get_charts_from_spreadsheet(response)

        assert len(charts) == 2
        assert charts[0].sheet_name == "Sheet1"
        assert charts[1].sheet_name == "Sheet2"

    def test_empty_response(self) -> None:
        """Test empty sheets list returns empty charts."""
        response: dict[str, Any] = {"sheets": []}
        charts = get_charts_from_spreadsheet(response)
        assert charts == []

    def test_sheet_without_charts(self) -> None:
        """Test sheet with no charts key."""
        response = {
            "sheets": [{
                "properties": {"title": "Data Only"}
                # No "charts" key
            }]
        }

        charts = get_charts_from_spreadsheet(response)
        assert charts == []

    def test_chart_without_title(self) -> None:
        """Test chart with no title in spec."""
        response = {
            "sheets": [{
                "properties": {"title": "Data"},
                "charts": [{
                    "chartId": 123,
                    "spec": {"basicChart": {"chartType": "LINE"}}
                    # No "title" in spec
                }]
            }]
        }

        charts = get_charts_from_spreadsheet(response)

        assert len(charts) == 1
        assert charts[0].title is None
        assert charts[0].chart_type == "LINE"

    def test_unknown_chart_type(self) -> None:
        """Test chart with unknown type (not basic/pie/histogram)."""
        response = {
            "sheets": [{
                "properties": {"title": "Data"},
                "charts": [{
                    "chartId": 123,
                    "spec": {
                        "title": "Mystery Chart",
                        "someUnknownChart": {}
                    }
                }]
            }]
        }

        charts = get_charts_from_spreadsheet(response)

        assert len(charts) == 1
        assert charts[0].chart_type is None


class TestRenderChartsAsPngs:
    """Tests for chart rendering via Slides API."""

    def test_empty_charts_returns_early(self) -> None:
        """Test that empty charts list returns immediately without API calls."""
        charts, render_time = render_charts_as_pngs("spreadsheet-id", [])

        assert charts == []
        assert render_time == 0

    def test_happy_path_renders_charts(
        self, mock_slides_service: MagicMock, mock_drive_service: MagicMock
    ) -> None:
        """Test successful chart rendering flow."""
        charts = [
            ChartData(chart_id=1, title="Chart 1", sheet_name="Data"),
            ChartData(chart_id=2, title="Chart 2", sheet_name="Data"),
        ]

        # Mock time.time to make objectIds predictable
        mock_time = MagicMock()
        mock_time.time.return_value = 12345
        mock_time.perf_counter.side_effect = [0.0, 1.0]  # For render timing

        mock_api_chain(mock_slides_service, "presentations.create.execute", {
            "presentationId": "temp-pres-id"
        })
        mock_api_chain(mock_slides_service, "presentations.batchUpdate.execute", {})
        mock_api_chain(mock_slides_service, "presentations.get.execute", {
            "slides": [
                {
                    "pageElements": [{
                        "objectId": "chart_0_12345",
                        "sheetsChart": {"contentUrl": "http://example.com/chart1.png"}
                    }]
                },
                {
                    "pageElements": [{
                        "objectId": "chart_1_12345",
                        "sheetsChart": {"contentUrl": "http://example.com/chart2.png"}
                    }]
                },
            ]
        })

        with patch("adapters.charts.get_slides_service", return_value=mock_slides_service), \
             patch("adapters.charts.get_drive_service", return_value=mock_drive_service), \
             patch("adapters.charts.requests.get") as mock_get, \
             patch("adapters.charts.time", mock_time):

            # Mock PNG downloads (must be > 100 bytes to pass the size check)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b"\x89PNG\r\n\x1a\n" + b"x" * 200  # > 100 bytes
            mock_get.return_value = mock_response

            result_charts, render_time = render_charts_as_pngs("spreadsheet-id", charts)

        assert len(result_charts) == 2
        assert result_charts[0].png_bytes is not None
        assert result_charts[1].png_bytes is not None
        assert render_time == 1000  # 1.0 - 0.0 seconds = 1000ms

        # Verify presentation was deleted
        mock_drive_service.files.return_value.delete.assert_called_once_with(fileId="temp-pres-id")

    def test_missing_content_url_graceful(
        self, mock_slides_service: MagicMock, mock_drive_service: MagicMock
    ) -> None:
        """Test that missing contentUrl doesn't crash, chart just has no PNG."""
        charts = [
            ChartData(chart_id=1, title="Good Chart", sheet_name="Data"),
            ChartData(chart_id=2, title="Missing URL", sheet_name="Data"),
        ]

        # Mock time.time to make objectIds predictable
        mock_time = MagicMock()
        mock_time.time.return_value = 12345
        mock_time.perf_counter.side_effect = [0.0, 1.0]

        mock_api_chain(mock_slides_service, "presentations.create.execute", {
            "presentationId": "temp-pres-id"
        })
        mock_api_chain(mock_slides_service, "presentations.batchUpdate.execute", {})
        # First chart has contentUrl, second doesn't
        mock_api_chain(mock_slides_service, "presentations.get.execute", {
            "slides": [
                {
                    "pageElements": [{
                        "objectId": "chart_0_12345",
                        "sheetsChart": {"contentUrl": "http://example.com/chart1.png"}
                    }]
                },
                {
                    "pageElements": [{
                        "objectId": "chart_1_12345",
                        "sheetsChart": {}  # No contentUrl
                    }]
                },
            ]
        })

        with patch("adapters.charts.get_slides_service", return_value=mock_slides_service), \
             patch("adapters.charts.get_drive_service", return_value=mock_drive_service), \
             patch("adapters.charts.requests.get") as mock_get, \
             patch("adapters.charts.time", mock_time):

            # Mock PNG downloads (must be > 100 bytes)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b"\x89PNG\r\n\x1a\n" + b"x" * 200
            mock_get.return_value = mock_response

            result_charts, _ = render_charts_as_pngs("spreadsheet-id", charts)

        # First chart got PNG, second didn't (no contentUrl)
        assert result_charts[0].png_bytes is not None
        assert result_charts[1].png_bytes is None

    def test_png_fetch_failure_graceful(
        self, mock_slides_service: MagicMock, mock_drive_service: MagicMock
    ) -> None:
        """Test that PNG download failure doesn't crash."""
        charts = [ChartData(chart_id=1, title="Chart", sheet_name="Data")]

        mock_api_chain(mock_slides_service, "presentations.create.execute", {
            "presentationId": "temp-pres-id"
        })
        mock_api_chain(mock_slides_service, "presentations.batchUpdate.execute", {})
        mock_api_chain(mock_slides_service, "presentations.get.execute", {
            "slides": [{
                "pageElements": [{
                    "objectId": "chart_0_12345",
                    "sheetsChart": {"contentUrl": "http://example.com/chart.png"}
                }]
            }]
        })

        with patch("adapters.charts.get_slides_service", return_value=mock_slides_service), \
             patch("adapters.charts.get_drive_service", return_value=mock_drive_service), \
             patch("adapters.charts.requests.get") as mock_get, \
             patch("adapters.charts.time.time", return_value=12345):

            # Simulate network error
            mock_get.side_effect = requests.RequestException("Connection failed")

            result_charts, _ = render_charts_as_pngs("spreadsheet-id", charts)

        # Chart has no PNG but didn't crash
        assert result_charts[0].png_bytes is None

    def test_png_too_small_rejected(
        self, mock_slides_service: MagicMock, mock_drive_service: MagicMock
    ) -> None:
        """Test that tiny responses (likely errors) are rejected."""
        charts = [ChartData(chart_id=1, title="Chart", sheet_name="Data")]

        mock_api_chain(mock_slides_service, "presentations.create.execute", {
            "presentationId": "temp-pres-id"
        })
        mock_api_chain(mock_slides_service, "presentations.batchUpdate.execute", {})
        mock_api_chain(mock_slides_service, "presentations.get.execute", {
            "slides": [{
                "pageElements": [{
                    "objectId": "chart_0_12345",
                    "sheetsChart": {"contentUrl": "http://example.com/chart.png"}
                }]
            }]
        })

        with patch("adapters.charts.get_slides_service", return_value=mock_slides_service), \
             patch("adapters.charts.get_drive_service", return_value=mock_drive_service), \
             patch("adapters.charts.requests.get") as mock_get, \
             patch("adapters.charts.time.time", return_value=12345):

            # Return tiny response (< 100 bytes threshold)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b"error"  # Only 5 bytes
            mock_get.return_value = mock_response

            result_charts, _ = render_charts_as_pngs("spreadsheet-id", charts)

        assert result_charts[0].png_bytes is None

    def test_presentation_cleanup_on_error(
        self, mock_slides_service: MagicMock, mock_drive_service: MagicMock
    ) -> None:
        """Test that temporary presentation is deleted even when error occurs."""
        charts = [ChartData(chart_id=1, title="Chart", sheet_name="Data")]

        mock_api_chain(mock_slides_service, "presentations.create.execute", {
            "presentationId": "temp-pres-id"
        })
        # batchUpdate fails
        mock_api_chain(mock_slides_service, "presentations.batchUpdate.execute",
                       side_effect=Exception("API error"))

        with patch("adapters.charts.get_slides_service", return_value=mock_slides_service), \
             patch("adapters.charts.get_drive_service", return_value=mock_drive_service):

            with pytest.raises(Exception, match="API error"):
                render_charts_as_pngs("spreadsheet-id", charts)

        # Verify cleanup still happened
        mock_drive_service.files().delete.assert_called_once_with(fileId="temp-pres-id")

    def test_presentation_creation_failure(
        self, mock_slides_service: MagicMock, mock_drive_service: MagicMock
    ) -> None:
        """Test error when presentation creation returns no ID."""
        charts = [ChartData(chart_id=1, title="Chart", sheet_name="Data")]

        mock_api_chain(mock_slides_service, "presentations.create.execute", {})  # No presentationId

        with patch("adapters.charts.get_slides_service", return_value=mock_slides_service), \
             patch("adapters.charts.get_drive_service", return_value=mock_drive_service):

            with pytest.raises(RuntimeError, match="Failed to create presentation"):
                render_charts_as_pngs("spreadsheet-id", charts)
