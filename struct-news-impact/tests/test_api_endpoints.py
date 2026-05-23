"""
Integration tests — Flask API endpoints
Uses Flask test client with mocked FinnHub cache.
Tests HTTP status codes, response shapes, and error handling.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
import json
import calendar_fetcher


def _make_event(country, event_name, impact, minutes_from_now):
    t = datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)
    return {
        "country": country, "event": event_name, "impact": impact,
        "time": t.strftime("%Y-%m-%d %H:%M:%S"),
        "actual": None, "estimate": None, "prev": None, "unit": "",
    }


CLEAN_STATUS = {"last_error": None}
STALE_STATUS = {"last_error": "Connection refused"}


@pytest.fixture
def client():
    os.environ["FINNHUB_API_KEY"] = "test_key_for_tests"
    import news_impact_server
    news_impact_server.app.config["TESTING"] = True
    # Ensure the module has the key initialised without hitting FinnHub
    calendar_fetcher.init("test_key_for_tests")
    calendar_fetcher._cache = []
    calendar_fetcher._last_refresh = 9999999999.0   # mark as fresh
    calendar_fetcher._last_error = None
    with news_impact_server.app.test_client() as c:
        yield c


# ── /api/impact/health ────────────────────────────────────────────────────────

class TestHealthEndpoint:
    @patch("calendar_fetcher.get_status")
    def test_returns_200(self, mock_status, client):
        mock_status.return_value = {
            "events_cached": 50, "last_error": None,
            "last_refresh_utc": "2026-06-10 12:00:00 UTC",
            "cache_age_secs": 120, "next_refresh_secs": 3480, "api_key_set": True,
        }
        resp = client.get("/api/impact/health")
        assert resp.status_code == 200

    @patch("calendar_fetcher.get_status")
    def test_status_ok_when_no_error(self, mock_status, client):
        mock_status.return_value = {
            "events_cached": 50, "last_error": None,
            "last_refresh_utc": "2026-06-10 12:00:00 UTC",
            "cache_age_secs": 120, "next_refresh_secs": 3480, "api_key_set": True,
        }
        data = client.get("/api/impact/health").get_json()
        assert data["status"] == "ok"
        assert data["api_key_set"] is True

    @patch("calendar_fetcher.get_status")
    def test_status_degraded_when_error(self, mock_status, client):
        mock_status.return_value = {
            "events_cached": 0, "last_error": "Connection refused",
            "last_refresh_utc": None, "cache_age_secs": None,
            "next_refresh_secs": 0, "api_key_set": True,
        }
        data = client.get("/api/impact/health").get_json()
        assert data["status"] == "degraded"

    @patch("calendar_fetcher.get_status")
    def test_includes_active_pairs(self, mock_status, client):
        mock_status.return_value = {
            "events_cached": 10, "last_error": None,
            "last_refresh_utc": "2026-06-10 12:00:00 UTC",
            "cache_age_secs": 0, "next_refresh_secs": 3600, "api_key_set": True,
        }
        data = client.get("/api/impact/health").get_json()
        assert "active_pairs" in data
        assert isinstance(data["active_pairs"], list)
        assert len(data["active_pairs"]) > 0


# ── /api/impact/symbol ────────────────────────────────────────────────────────

class TestSymbolEndpoint:
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_returns_200_for_valid_pair(self, mock_status, mock_events, client):
        mock_events.return_value = []
        mock_status.return_value = CLEAN_STATUS
        resp = client.get("/api/impact/symbol?pair=USD/JPY")
        assert resp.status_code == 200

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_returns_correct_pair_in_body(self, mock_status, mock_events, client):
        mock_events.return_value = []
        mock_status.return_value = CLEAN_STATUS
        data = client.get("/api/impact/symbol?pair=EUR/USD").get_json()
        assert data["pair"] == "EUR/USD"

    def test_returns_400_without_pair_param(self, client):
        resp = client.get("/api/impact/symbol")
        assert resp.status_code == 400

    def test_returns_400_for_unknown_pair(self, client):
        resp = client.get("/api/impact/symbol?pair=BTC/USD")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data
        assert "known" in data

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_blocked_true_for_active_nfp(self, mock_status, mock_events, client):
        mock_events.return_value = [_make_event("US", "Non-Farm Payrolls", "high", 10)]
        mock_status.return_value = CLEAN_STATUS
        data = client.get("/api/impact/symbol?pair=USD/JPY").get_json()
        assert data["blocked"] is True
        assert data["confidence_penalty"] == 100

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_response_has_all_required_fields(self, mock_status, mock_events, client):
        mock_events.return_value = []
        mock_status.return_value = CLEAN_STATUS
        data = client.get("/api/impact/symbol?pair=GBP/USD").get_json()
        required = ["pair", "blocked", "confidence_penalty", "impact_level",
                    "reason", "active_events", "upcoming_events", "source", "checked_utc"]
        for key in required:
            assert key in data, f"Missing key: {key}"

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_pair_param_case_insensitive(self, mock_status, mock_events, client):
        mock_events.return_value = []
        mock_status.return_value = CLEAN_STATUS
        resp = client.get("/api/impact/symbol?pair=usd/jpy")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["pair"] == "USD/JPY"

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_all_active_pairs_return_200(self, mock_status, mock_events, client):
        mock_events.return_value = []
        mock_status.return_value = CLEAN_STATUS
        import pair_mapper
        for pair in pair_mapper.ACTIVE_PAIRS:
            resp = client.get(f"/api/impact/symbol?pair={pair}")
            assert resp.status_code == 200, f"Failed for pair {pair}"


# ── /api/impact/now ───────────────────────────────────────────────────────────

class TestNowEndpoint:
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_returns_200(self, mock_status, mock_events, client):
        mock_events.return_value = []
        mock_status.return_value = CLEAN_STATUS
        resp = client.get("/api/impact/now")
        assert resp.status_code == 200

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_returns_all_active_pairs(self, mock_status, mock_events, client):
        mock_events.return_value = []
        mock_status.return_value = CLEAN_STATUS
        data = client.get("/api/impact/now").get_json()
        import pair_mapper
        for pair in pair_mapper.ACTIVE_PAIRS:
            assert pair in data

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_blocked_pair_reflected_in_now(self, mock_status, mock_events, client):
        mock_events.return_value = [_make_event("US", "Non-Farm Payrolls", "high", 5)]
        mock_status.return_value = CLEAN_STATUS
        data = client.get("/api/impact/now").get_json()
        assert data["USD/JPY"]["blocked"] is True


# ── /api/impact/upcoming ──────────────────────────────────────────────────────

class TestUpcomingEndpoint:
    @patch("calendar_fetcher.get_events")
    def test_returns_200(self, mock_events, client):
        mock_events.return_value = []
        resp = client.get("/api/impact/upcoming")
        assert resp.status_code == 200

    @patch("calendar_fetcher.get_events")
    def test_default_hours_is_24(self, mock_events, client):
        mock_events.return_value = []
        data = client.get("/api/impact/upcoming").get_json()
        assert data["hours"] == 24

    @patch("calendar_fetcher.get_events")
    def test_custom_hours_respected(self, mock_events, client):
        mock_events.return_value = []
        data = client.get("/api/impact/upcoming?hours=48").get_json()
        assert data["hours"] == 48

    @patch("calendar_fetcher.get_events")
    def test_hours_capped_at_72(self, mock_events, client):
        mock_events.return_value = []
        data = client.get("/api/impact/upcoming?hours=9999").get_json()
        assert data["hours"] == 72

    @patch("calendar_fetcher.get_events")
    def test_hours_minimum_1(self, mock_events, client):
        mock_events.return_value = []
        data = client.get("/api/impact/upcoming?hours=0").get_json()
        assert data["hours"] == 1

    @patch("calendar_fetcher.get_events")
    def test_invalid_hours_defaults_to_24(self, mock_events, client):
        mock_events.return_value = []
        data = client.get("/api/impact/upcoming?hours=abc").get_json()
        assert data["hours"] == 24

    @patch("calendar_fetcher.get_events")
    def test_events_field_is_list(self, mock_events, client):
        mock_events.return_value = []
        data = client.get("/api/impact/upcoming").get_json()
        assert isinstance(data["events"], list)

    @patch("calendar_fetcher.get_events")
    def test_upcoming_event_shows_in_response(self, mock_events, client):
        mock_events.return_value = [_make_event("US", "CPI MoM", "high", 60)]
        data = client.get("/api/impact/upcoming?hours=24").get_json()
        assert data["total_events"] == 1
        assert data["events"][0]["event"] == "CPI MoM"


# ── /api/impact/refresh ───────────────────────────────────────────────────────

class TestRefreshEndpoint:
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_returns_200(self, mock_status, mock_events, client):
        mock_events.return_value = []
        mock_status.return_value = CLEAN_STATUS
        resp = client.post("/api/impact/refresh")
        assert resp.status_code == 200

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_calls_force_refresh(self, mock_status, mock_events, client):
        mock_events.return_value = []
        mock_status.return_value = CLEAN_STATUS
        client.post("/api/impact/refresh")
        mock_events.assert_called_once_with(force_refresh=True)

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_response_ok_true_on_success(self, mock_status, mock_events, client):
        mock_events.return_value = []
        mock_status.return_value = CLEAN_STATUS
        data = client.post("/api/impact/refresh").get_json()
        assert data["ok"] is True

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_response_ok_false_on_error(self, mock_status, mock_events, client):
        mock_events.return_value = []
        mock_status.return_value = {"last_error": "Timeout"}
        data = client.post("/api/impact/refresh").get_json()
        assert data["ok"] is False

    def test_get_on_refresh_returns_405(self, client):
        resp = client.get("/api/impact/refresh")
        assert resp.status_code == 405
