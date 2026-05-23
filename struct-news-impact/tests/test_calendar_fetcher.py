"""
Unit tests — calendar_fetcher.py
All FinnHub HTTP calls are mocked — no real API key needed.
"""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock
import calendar_fetcher


# Reset module state between tests
@pytest.fixture(autouse=True)
def reset_fetcher():
    calendar_fetcher._cache = []
    calendar_fetcher._last_refresh = 0.0
    calendar_fetcher._last_error = None
    calendar_fetcher._api_key = "test_key_123"
    yield
    calendar_fetcher._cache = []
    calendar_fetcher._last_refresh = 0.0
    calendar_fetcher._last_error = None
    calendar_fetcher._api_key = None


SAMPLE_EVENTS = [
    {"country": "US", "event": "CPI MoM", "impact": "high",
     "time": "2026-06-10 12:30:00", "actual": None, "estimate": "0.3", "prev": "0.4", "unit": "%"},
    {"country": "GB", "event": "Retail Sales MoM", "impact": "high",
     "time": "2026-06-10 06:00:00", "actual": None, "estimate": "0.2", "prev": "-0.1", "unit": "%"},
    {"country": "EU", "event": "ECB Rate Decision", "impact": "high",
     "time": "2026-06-11 11:45:00", "actual": None, "estimate": None, "prev": "3.5", "unit": "%"},
]


def _mock_success(events=None):
    """Returns a mock requests.get that succeeds with the given events."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"economicCalendar": SAMPLE_EVENTS if events is None else events}
    return mock_resp


def _mock_http_error():
    import requests
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("429 Too Many Requests")
    return mock_resp


def _mock_network_error():
    import requests
    mock = MagicMock()
    mock.side_effect = requests.ConnectionError("Connection refused")
    return mock


# ── init ──────────────────────────────────────────────────────────────────────

class TestInit:
    def test_sets_api_key(self):
        calendar_fetcher.init("my_key_xyz")
        assert calendar_fetcher._api_key == "my_key_xyz"

    def test_overwrites_existing_key(self):
        calendar_fetcher.init("key1")
        calendar_fetcher.init("key2")
        assert calendar_fetcher._api_key == "key2"


# ── get_events ────────────────────────────────────────────────────────────────

class TestGetEvents:
    @patch("requests.get")
    def test_returns_events_on_success(self, mock_get):
        mock_get.return_value = _mock_success()
        events = calendar_fetcher.get_events(force_refresh=True)
        assert len(events) == 3
        assert events[0]["event"] == "CPI MoM"

    @patch("requests.get")
    def test_caches_result(self, mock_get):
        mock_get.return_value = _mock_success()
        calendar_fetcher.get_events(force_refresh=True)
        calendar_fetcher.get_events()
        # Only called once — second call used cache
        assert mock_get.call_count == 1

    @patch("requests.get")
    def test_force_refresh_bypasses_cache(self, mock_get):
        mock_get.return_value = _mock_success()
        calendar_fetcher.get_events(force_refresh=True)
        calendar_fetcher.get_events(force_refresh=True)
        assert mock_get.call_count == 2

    @patch("requests.get")
    def test_stale_cache_triggers_refresh(self, mock_get):
        mock_get.return_value = _mock_success()
        # Prime the cache but set last_refresh to old time
        calendar_fetcher.get_events(force_refresh=True)
        calendar_fetcher._last_refresh = time.time() - calendar_fetcher.REFRESH_SECS - 1
        calendar_fetcher.get_events()
        assert mock_get.call_count == 2

    @patch("requests.get")
    def test_fresh_cache_not_refreshed(self, mock_get):
        mock_get.return_value = _mock_success()
        calendar_fetcher.get_events(force_refresh=True)
        calendar_fetcher._last_refresh = time.time()   # just refreshed
        calendar_fetcher.get_events()
        assert mock_get.call_count == 1

    @patch("requests.get")
    def test_network_error_returns_old_cache(self, mock_get):
        # Prime with good data first
        mock_get.return_value = _mock_success()
        calendar_fetcher.get_events(force_refresh=True)
        first_count = len(calendar_fetcher._cache)

        # Now fail
        mock_get.side_effect = Exception("Network down")
        calendar_fetcher._last_refresh = 0  # force refresh
        events = calendar_fetcher.get_events()
        assert len(events) == first_count  # still returns old cache
        assert calendar_fetcher._last_error is not None

    @patch("requests.get")
    def test_empty_cache_on_first_failure(self, mock_get):
        mock_get.side_effect = Exception("Connection refused")
        events = calendar_fetcher.get_events()
        assert events == []
        assert calendar_fetcher._last_error is not None

    @patch("requests.get")
    def test_returns_copy_not_reference(self, mock_get):
        mock_get.return_value = _mock_success()
        events1 = calendar_fetcher.get_events(force_refresh=True)
        events2 = calendar_fetcher.get_events()
        assert events1 is not events2   # different list objects

    @patch("calendar_fetcher.requests.get")
    def test_empty_calendar_from_api(self, mock_get):
        mock_get.return_value = _mock_success(events=[])
        events = calendar_fetcher.get_events(force_refresh=True)
        assert events == []
        assert calendar_fetcher._last_error is None

    def test_no_api_key_records_error_in_status(self):
        calendar_fetcher._api_key = None
        calendar_fetcher._do_refresh()
        # _do_refresh catches the RuntimeError internally and stores it
        assert calendar_fetcher._last_error is not None
        assert "key" in calendar_fetcher._last_error.lower()


# ── get_status ────────────────────────────────────────────────────────────────

class TestGetStatus:
    @patch("requests.get")
    def test_ok_status_after_refresh(self, mock_get):
        mock_get.return_value = _mock_success()
        calendar_fetcher.get_events(force_refresh=True)
        status = calendar_fetcher.get_status()
        assert status["last_error"] is None
        assert status["events_cached"] == 3
        assert status["api_key_set"] is True

    def test_status_before_any_refresh(self):
        status = calendar_fetcher.get_status()
        assert status["last_refresh_utc"] is None
        assert status["cache_age_secs"] is None

    @patch("requests.get")
    def test_error_recorded_in_status(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        calendar_fetcher.get_events(force_refresh=True)
        status = calendar_fetcher.get_status()
        assert status["last_error"] is not None
        assert "timeout" in status["last_error"]

    @patch("requests.get")
    def test_next_refresh_secs_decreases_over_time(self, mock_get):
        mock_get.return_value = _mock_success()
        calendar_fetcher.get_events(force_refresh=True)
        s1 = calendar_fetcher.get_status()["next_refresh_secs"]
        # Artificially age the cache by 10 seconds
        calendar_fetcher._last_refresh -= 10
        s2 = calendar_fetcher.get_status()["next_refresh_secs"]
        assert s2 <= s1
