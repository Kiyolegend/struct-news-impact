"""
Service lifecycle, resilience, idempotency, and edge-breaking tests.

Tests the complete service layer — startup states, restart recovery,
config validation, concurrent request safety, and extreme data volumes.
No real network calls are made; FinnHub is always mocked.
"""

import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import calendar_fetcher
import impact_scorer
import pair_mapper


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_fetcher():
    calendar_fetcher._cache        = []
    calendar_fetcher._last_refresh = 0.0
    calendar_fetcher._last_error   = None
    calendar_fetcher._api_key      = "test_key_123"
    yield
    calendar_fetcher._cache        = []
    calendar_fetcher._last_refresh = 0.0
    calendar_fetcher._last_error   = None
    calendar_fetcher._api_key      = None


@pytest.fixture
def flask_client():
    import news_impact_server
    news_impact_server.app.config["TESTING"] = True
    with news_impact_server.app.test_client() as client:
        yield client


def _mock_resp(payload):
    m = MagicMock()
    m.raise_for_status = MagicMock()
    m.json.return_value = payload
    return m


def _event(country, name, impact, minutes_offset, actual=None, estimate="0.3"):
    now = datetime.now(timezone.utc)
    t   = now + timedelta(minutes=minutes_offset)
    return {
        "country": country, "event": name, "impact": impact,
        "time": t.strftime("%Y-%m-%d %H:%M:%S"),
        "actual": actual, "estimate": estimate, "prev": "0.2", "unit": "%",
    }


def _finnhub(events):
    return _mock_resp({"economicCalendar": events})


# ── TestServiceStartup ────────────────────────────────────────────────────────

class TestServiceStartup:
    def test_health_returns_degraded_with_empty_cache(self, flask_client):
        """Health is 'degraded' immediately after startup before any fetch."""
        rv = flask_client.get("/api/impact/health")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["status"] == "degraded"

    @patch("calendar_fetcher.requests.get")
    def test_health_returns_ok_after_successful_fetch(self, mock_get, flask_client):
        """Health becomes 'ok' after a successful FinnHub fetch."""
        mock_get.return_value = _finnhub([_event("US", "CPI MoM", "high", 60)])
        calendar_fetcher.get_events(force_refresh=True)
        rv = flask_client.get("/api/impact/health")
        data = rv.get_json()
        assert data["status"] == "ok"
        assert data["events_cached"] >= 1

    def test_all_endpoints_respond_before_any_fetch(self, flask_client):
        """Every endpoint returns 200 even with an empty cache — no crashes."""
        assert flask_client.get("/api/impact/health").status_code == 200
        assert flask_client.get("/api/impact/now").status_code == 200
        assert flask_client.get("/api/impact/symbol?pair=USD/JPY").status_code == 200
        assert flask_client.get("/api/impact/upcoming").status_code == 200

    def test_health_reports_api_key_set(self, flask_client):
        """Health endpoint shows api_key_set=True when key is configured."""
        rv = flask_client.get("/api/impact/health")
        data = rv.get_json()
        assert data["api_key_set"] is True

    def test_health_includes_all_active_pairs(self, flask_client):
        """Health lists all 5 active pairs in the response."""
        rv = flask_client.get("/api/impact/health")
        data = rv.get_json()
        pairs = set(data["active_pairs"])
        assert pairs == {"USD/JPY", "EUR/USD", "GBP/USD", "AUD/USD", "USD/CHF"}

    def test_server_port_read_from_env(self):
        """Server reads port from NEWS_IMPACT_PORT env var."""
        import news_impact_server
        assert news_impact_server.PORT == int(os.getenv("NEWS_IMPACT_PORT", 5003))

    def test_health_shows_api_key_not_set_when_missing(self, flask_client):
        """Health reports api_key_set=False when key is cleared."""
        calendar_fetcher._api_key = None
        rv = flask_client.get("/api/impact/health")
        data = rv.get_json()
        assert data["api_key_set"] is False


# ── TestServiceRestart ────────────────────────────────────────────────────────

class TestServiceRestart:
    @patch("calendar_fetcher.requests.get")
    def test_cache_recovers_after_state_reset(self, mock_get, flask_client):
        """After state reset (simulating restart), first query refetches from FinnHub."""
        mock_get.return_value = _finnhub([_event("US", "CPI MoM", "high", 60)])
        # Simulate restart: state is reset by autouse fixture
        rv = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        # The request triggers a cache refresh automatically
        assert rv.status_code == 200
        # mock_get should have been called to fetch fresh data
        assert mock_get.called

    @patch("calendar_fetcher.requests.get")
    def test_stale_cache_survives_finnhub_outage(self, mock_get, flask_client):
        """If FinnHub goes down after initial load, stale cache keeps service running."""
        # First: load cache successfully
        mock_get.return_value = _finnhub([_event("US", "NFP", "high", -10)])
        calendar_fetcher.get_events(force_refresh=True)

        # Then: FinnHub goes down
        mock_get.side_effect = Exception("Connection refused")
        calendar_fetcher._last_refresh = 0.0  # force stale

        rv = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        assert rv.status_code == 200
        data = rv.get_json()
        # Should still respond using stale cache
        assert "blocked" in data

    @patch("calendar_fetcher.requests.get")
    def test_all_pairs_respond_after_state_reset(self, mock_get, flask_client):
        """All 5 active pairs return valid responses after a restart."""
        mock_get.return_value = _finnhub([])
        for pair in ["USD/JPY", "EUR/USD", "GBP/USD", "AUD/USD", "USD/CHF"]:
            rv = flask_client.get(f"/api/impact/symbol?pair={pair}")
            assert rv.status_code == 200, f"Pair {pair} failed after restart"

    @patch("calendar_fetcher.requests.get")
    def test_force_refresh_restores_after_error(self, mock_get, flask_client):
        """Force-refresh clears a previous fetch error and updates data."""
        # First attempt fails
        mock_get.side_effect = Exception("Timeout")
        calendar_fetcher._do_refresh()
        assert calendar_fetcher._last_error is not None

        # Recovery: next fetch succeeds
        mock_get.side_effect = None
        mock_get.return_value = _finnhub([_event("US", "CPI MoM", "high", 30)])
        rv = flask_client.post("/api/impact/refresh")
        data = rv.get_json()
        assert data["ok"] is True
        assert calendar_fetcher._last_error is None

    @patch("calendar_fetcher.requests.get")
    def test_repeated_restarts_stay_consistent(self, mock_get, flask_client):
        """Simulating 3 restarts (state resets + refetches) gives consistent results."""
        mock_get.return_value = _finnhub([_event("US", "CPI MoM", "high", 60)])
        for _ in range(3):
            calendar_fetcher._cache        = []
            calendar_fetcher._last_refresh = 0.0
            calendar_fetcher._last_error   = None
            rv = flask_client.get("/api/impact/symbol?pair=EUR/USD")
            assert rv.status_code == 200
            assert "blocked" in rv.get_json()


# ── TestConfigValidation ──────────────────────────────────────────────────────

class TestConfigValidation:
    def test_missing_api_key_health_still_responds(self, flask_client):
        """Service does not crash when API key is missing — health returns 200."""
        calendar_fetcher._api_key = None
        rv = flask_client.get("/api/impact/health")
        assert rv.status_code == 200

    def test_missing_api_key_causes_degraded_status(self, flask_client):
        """Health reports 'degraded' when no API key is set."""
        calendar_fetcher._api_key = None
        calendar_fetcher._do_refresh()
        rv = flask_client.get("/api/impact/health")
        data = rv.get_json()
        assert data["status"] == "degraded"

    def test_symbol_endpoint_with_empty_cache_returns_clear(self, flask_client):
        """Symbol endpoint returns blocked=False when cache is empty (no events = clear)."""
        rv = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        data = rv.get_json()
        assert data["blocked"] is False

    def test_now_endpoint_returns_all_pairs_with_empty_cache(self, flask_client):
        """Now endpoint returns all 5 pairs even when cache is empty."""
        rv = flask_client.get("/api/impact/now")
        data = rv.get_json()
        assert set(data.keys()) == {"USD/JPY", "EUR/USD", "GBP/USD", "AUD/USD", "USD/CHF"}

    def test_upcoming_returns_empty_list_with_empty_cache(self, flask_client):
        """Upcoming endpoint returns empty event list when cache is empty."""
        rv = flask_client.get("/api/impact/upcoming")
        data = rv.get_json()
        assert data["events"] == []
        assert data["total_events"] == 0


# ── TestIdempotency ───────────────────────────────────────────────────────────

class TestIdempotency:
    @patch("calendar_fetcher.requests.get")
    def test_multiple_symbol_calls_return_same_result(self, mock_get, flask_client):
        """Calling symbol endpoint 5 times in a row returns identical results."""
        mock_get.return_value = _finnhub([_event("US", "CPI MoM", "high", -10)])
        calendar_fetcher.get_events(force_refresh=True)

        results = [
            flask_client.get("/api/impact/symbol?pair=USD/JPY").get_json()
            for _ in range(5)
        ]
        # All results should be identical (same blocked state)
        for r in results[1:]:
            assert r["blocked"] == results[0]["blocked"]
            assert r["confidence_penalty"] == results[0]["confidence_penalty"]

    @patch("calendar_fetcher.requests.get")
    def test_multiple_refreshes_do_not_duplicate_events(self, mock_get, flask_client):
        """Calling force-refresh multiple times doesn't grow the event list."""
        mock_get.return_value = _finnhub([_event("US", "CPI MoM", "high", 60)])
        for _ in range(3):
            flask_client.post("/api/impact/refresh")
        count = calendar_fetcher.get_status()["events_cached"]
        assert count == 1

    @patch("calendar_fetcher.requests.get")
    def test_concurrent_requests_return_consistent_data(self, mock_get):
        """10 concurrent threads each calling the scorer directly all get consistent results."""
        mock_get.return_value = _finnhub([_event("US", "CPI MoM", "high", -10)])
        calendar_fetcher.get_events(force_refresh=True)

        results = []
        errors  = []
        lock    = threading.Lock()

        def call():
            try:
                data = impact_scorer.get_pair_impact("USD/JPY")
                with lock:
                    results.append(data)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=call) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert errors == [], f"Concurrent errors: {errors}"
        assert len(results) == 10
        # All threads must agree on blocked state
        blocked_states = {r["blocked"] for r in results}
        assert len(blocked_states) == 1, "Concurrent requests returned inconsistent blocked states"

    @patch("calendar_fetcher.requests.get")
    def test_refresh_then_query_is_consistent(self, mock_get, flask_client):
        """Force-refresh followed immediately by a query returns the fresh data."""
        mock_get.return_value = _finnhub([_event("US", "NFP", "high", -10)])
        flask_client.post("/api/impact/refresh")
        rv = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        data = rv.get_json()
        assert data["blocked"] is True


# ── TestEdgeBreaking ──────────────────────────────────────────────────────────

class TestEdgeBreaking:
    @patch("calendar_fetcher.requests.get")
    def test_1000_simultaneous_events_handled(self, mock_get, flask_client):
        """Service handles 1000 events in cache without errors or slowdown."""
        events = [
            _event("US", f"Event_{i}", "low", 30 + i)
            for i in range(1000)
        ]
        mock_get.return_value = _finnhub(events)
        calendar_fetcher.get_events(force_refresh=True)
        assert calendar_fetcher.get_status()["events_cached"] == 1000

        start = time.time()
        rv = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        elapsed = time.time() - start
        assert rv.status_code == 200
        assert elapsed < 2.0, f"1000-event query took {elapsed:.2f}s — too slow"

    @patch("calendar_fetcher.requests.get")
    def test_very_long_event_name_handled(self, mock_get, flask_client):
        """Events with very long names don't crash the scorer."""
        long_name = "CPI " + ("Measurement Index " * 50)
        mock_get.return_value = _finnhub([_event("US", long_name, "high", -10)])
        calendar_fetcher.get_events(force_refresh=True)
        rv = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        assert rv.status_code == 200

    @patch("calendar_fetcher.requests.get")
    def test_all_pairs_blocked_simultaneously(self, mock_get, flask_client):
        """When a US market-stopping event fires, all 5 active pairs are blocked."""
        mock_get.return_value = _finnhub([_event("US", "Non-Farm Payrolls", "high", -10)])
        calendar_fetcher.get_events(force_refresh=True)
        rv   = flask_client.get("/api/impact/now")
        data = rv.get_json()
        for pair in ["USD/JPY", "EUR/USD", "GBP/USD", "AUD/USD", "USD/CHF"]:
            assert data[pair]["blocked"] is True, f"{pair} should be blocked during NFP"

    @patch("calendar_fetcher.requests.get")
    def test_rapid_sequential_requests_dont_corrupt_state(self, mock_get, flask_client):
        """50 rapid sequential requests don't corrupt the cache or produce errors."""
        mock_get.return_value = _finnhub([_event("US", "CPI MoM", "high", -10)])
        calendar_fetcher.get_events(force_refresh=True)
        for i in range(50):
            rv = flask_client.get("/api/impact/symbol?pair=EUR/USD")
            assert rv.status_code == 200, f"Request {i} failed"

    @patch("calendar_fetcher.requests.get")
    def test_finnhub_data_change_on_refresh_updates_correctly(self, mock_get, flask_client):
        """If FinnHub returns different data on next refresh, service picks it up."""
        # First load: US CPI event
        mock_get.return_value = _finnhub([_event("US", "CPI MoM", "high", -10)])
        calendar_fetcher.get_events(force_refresh=True)
        rv1 = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        assert rv1.get_json()["blocked"] is True

        # Second load: no events (quiet session)
        mock_get.return_value = _finnhub([])
        calendar_fetcher.get_events(force_refresh=True)
        rv2 = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        assert rv2.get_json()["blocked"] is False

    @patch("calendar_fetcher.requests.get")
    def test_events_with_null_fields_dont_crash_scorer(self, mock_get, flask_client):
        """Events with null/missing optional fields don't cause exceptions."""
        mock_get.return_value = _finnhub([{
            "country": "US", "event": "CPI MoM", "impact": "high",
            "time": (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),
            "actual": None, "estimate": None, "prev": None, "unit": None,
        }])
        calendar_fetcher.get_events(force_refresh=True)
        rv = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        assert rv.status_code == 200

    @patch("calendar_fetcher.requests.get")
    def test_extreme_surprise_with_null_estimate_no_crash(self, mock_get, flask_client):
        """Surprise detection handles null estimate gracefully (returns 'none')."""
        mock_get.return_value = _finnhub([{
            "country": "US", "event": "CPI MoM", "impact": "high",
            "time": (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
            "actual": "0.8", "estimate": None, "prev": "0.3", "unit": "%",
        }])
        calendar_fetcher.get_events(force_refresh=True)
        rv = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        assert rv.status_code == 200
        ev = rv.get_json()["active_events"][0]
        assert ev["surprise_level"] == "none"   # can't compute without estimate
        assert ev["extra_post_mins"] == 0
