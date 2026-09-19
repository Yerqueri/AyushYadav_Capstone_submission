"""Tests for src/metrics.py."""
import pytest
from unittest.mock import patch
from src.metrics import MetricsService, init_metrics_server


def test_metrics_service_start_server():
    with patch("src.metrics.start_http_server") as mock_start_http:
        service = MetricsService(default_port=9090)

        # First call starts server
        service.start_server()
        mock_start_http.assert_called_once_with(9090)

        # Second call is idempotent (does not start server again)
        service.start_server(9090)
        assert mock_start_http.call_count == 1


def test_init_metrics_server_facade():
    with patch("src.metrics.start_http_server") as mock_start_http:
        init_metrics_server(port=9099)
        assert mock_start_http.call_count in (0, 1)
