from unittest.mock import MagicMock, patch

import pytest
import requests

from api_client import APIClient


class TestAPIClientInit:
    """Constructor and configuration."""

    def test_default_values(self):
        client = APIClient(base_url="https://example.com/api/")
        assert client.base_url == "https://example.com/api/"
        assert client.timeout == 30
        assert client.max_retries == 3
        assert isinstance(client.session, requests.Session)

    def test_custom_timeout_and_retries(self):
        client = APIClient(base_url="http://localhost/", timeout=10, max_retries=5)
        assert client.timeout == 10
        assert client.max_retries == 5


class TestAPIClientGet:
    """GET request construction and response parsing."""

    def test_returns_parsed_json(self):
        client = APIClient(base_url="https://example.com/api/")
        mock_response = MagicMock()
        mock_response.json.return_value = {"key": "value"}

        with patch.object(client.session, "request", return_value=mock_response) as mock_req:
            result = client.get("test-endpoint/")
            mock_req.assert_called_once_with(
                "GET", "https://example.com/api/test-endpoint/",
                timeout=30, params=None, headers=None
            )
            assert result == {"key": "value"}

    def test_passes_params_and_headers(self):
        client = APIClient(base_url="https://example.com/api/")
        mock_response = MagicMock()
        mock_response.json.return_value = {}

        with patch.object(client.session, "request", return_value=mock_response) as mock_req:
            client.get("data/", params={"page": 1}, headers={"Authorization": "Bearer x"})
            mock_req.assert_called_once_with(
                "GET", "https://example.com/api/data/",
                timeout=30, params={"page": 1}, headers={"Authorization": "Bearer x"}
            )


class TestRequestWithRetry:
    """Retry logic with exponential backoff."""

    def test_succeeds_on_first_attempt(self):
        client = APIClient(base_url="https://example.com/api/")
        mock_response = MagicMock()

        with patch.object(client.session, "request", return_value=mock_response) as mock_req:
            result = client._request_with_retry("GET", "https://example.com/api/test")
            assert result is mock_response
            assert mock_req.call_count == 1

    def test_retries_on_request_exception_then_succeeds(self):
        client = APIClient(base_url="https://example.com/api/")
        mock_fail = MagicMock()
        mock_fail.raise_for_status.side_effect = requests.RequestException("timeout")
        mock_success = MagicMock()

        with patch.object(client.session, "request", side_effect=[mock_fail, mock_success]) as mock_req:
            with patch("api_client.time.sleep", return_value=None) as mock_sleep:
                result = client._request_with_retry("GET", "https://example.com/api/test")
                assert result is mock_success
                assert mock_req.call_count == 2
                mock_sleep.assert_called_once_with(1)

    def test_exhausts_all_retries_and_raises(self):
        client = APIClient(base_url="https://example.com/api/", max_retries=2)
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.RequestException("boom")

        with patch.object(client.session, "request", return_value=mock_response):
            with patch("api_client.time.sleep", return_value=None):
                with pytest.raises(requests.RequestException, match="boom"):
                    client._request_with_retry("GET", "https://example.com/api/test")

    def test_retry_backoff_doubles(self):
        client = APIClient(base_url="https://example.com/api/", max_retries=4)
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.RequestException("fail")

        sleeps = []
        with patch.object(client.session, "request", return_value=mock_response):
            with patch("api_client.time.sleep", side_effect=lambda s: sleeps.append(s)):
                with pytest.raises(requests.RequestException):
                    client._request_with_retry("GET", "https://example.com/api/test")
        assert sleeps == [1, 2, 4]

    def test_successful_response_still_calls_raise_for_status(self):
        client = APIClient(base_url="https://example.com/api/")
        mock_response = MagicMock()

        with patch.object(client.session, "request", return_value=mock_response):
            client._request_with_retry("GET", "https://example.com/api/test")
            mock_response.raise_for_status.assert_called_once()

    def test_http_error_triggers_retry(self):
        client = APIClient(base_url="https://example.com/api/", max_retries=2)
        mock_fail = MagicMock()
        mock_fail.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        mock_success = MagicMock()

        with patch.object(client.session, "request", side_effect=[mock_fail, mock_success]):
            with patch("api_client.time.sleep", return_value=None):
                result = client._request_with_retry("GET", "https://example.com/api/test")
                assert result is mock_success
