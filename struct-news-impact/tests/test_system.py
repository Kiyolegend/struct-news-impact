"""
System / end-to-end tests — full pipeline from raw FinnHub data to API response.
No mocking of internal logic — only the HTTP call to FinnHub is mocked.
Tests the entire stack: fetcher → scorer → Flask endpoint.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import calendar_fetcher


@pytest.fixture(autouse=True)
def reset_fetcher():
    calendar_fetcher._cache = []
    calendar_fetcher._last_refresh = 0.0
    calendar_fetcher._last_error = None
    calendar_fetcher._api_key = "test_key"
    yield
    calendar_fetcher._cache = []
    calendar_fetcher._last_refresh = 0.0
    calendar_fetcher._last_error = None
    calendar_fetcher._api_key = None


def _future(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def _past(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


# Realistic FinnHub payload (same structure as real API)
REALISTIC_PAYLOAD = {
    "economicCalendar": [
        # Should block USD/JPY — NFP in 20 minutes
        {"country": "US", "event": "Non-Farm Payrolls",   "impact": "high",
         "time": None,  # filled per test
         "actual": None, "estimate": "195K", "prev": "187K", "unit": "K"},
        # Should raise EUR/USD — ECB rate in 2 hours (not in active window)
        {"country": "EU", "event": "ECB Rate Decision",    "impact": "high",
         "time": None,
         "actual": None, "estimate": None, "prev": "3.5%", "unit": "%"},
        # Low impact — should not block anything
        {"country": "GB", "event": "Baker Hughes Rig Count", "impact": "low",
         "time": None,
         "actual": None, "estimate": "410", "prev": "415", "unit": ""},
        # Already happened 3 hours ago — should not affect anything
        {"country": "US", "event": "Michigan Consumer Sentiment", "impact": "medium",
         "time": None,
         "actual": "68.5", "estimate": "67.0", "prev": "65.0", "unit": ""},
    ]
}


def _build_payload(nfp_mins, ecb_mins, bh_mins, mich_past_mins):
    import copy
    p = copy.deepcopy(REALISTIC_PAYLOAD)
    events = p["economicCalendar"]
    events[0]["time"] = _future(nfp_mins)
    events[1]["time"] = _future(ecb_mins)
    events[2]["time"] = _future(bh_mins)
    events[3]["time"] = _past(mich_past_mins)
    return p


def _mock_finnhub(payload):
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json.return_value = payload
    return r


@pytest.fixture
def flask_client():
    os.environ["FINNHUB_API_KEY"] = "test_key"
    import news_impact_server
    news_impact_server.app.config["TESTING"] = True
    calendar_fetcher.init("test_key")
    with news_impact_server.app.test_client() as c:
        yield c


# ── Scenario 1: NFP Day (full block all pairs) ────────────────────────────────

class TestScenarioNFPDay:
    @patch("requests.get")
    def test_all_usd_pairs_blocked_20min_before_nfp(self, mock_get, flask_client):
        mock_get.return_value = _mock_finnhub(_build_payload(
            nfp_mins=20, ecb_mins=120, bh_mins=60, mich_past_mins=180
        ))
        calendar_fetcher.get_events(force_refresh=True)

        data = flask_client.get("/api/impact/now").get_json()
        for pair in ["USD/JPY", "EUR/USD", "GBP/USD", "AUD/USD", "USD/CHF"]:
            assert data[pair]["blocked"] is True, f"{pair} should be blocked during NFP"
            assert data[pair]["confidence_penalty"] == 100

    @patch("requests.get")
    def test_health_ok_during_nfp(self, mock_get, flask_client):
        mock_get.return_value = _mock_finnhub(_build_payload(20, 120, 60, 180))
        calendar_fetcher.get_events(force_refresh=True)
        data = flask_client.get("/api/impact/health").get_json()
        assert data["status"] == "ok"

    @patch("requests.get")
    def test_nfp_reason_contains_event_name(self, mock_get, flask_client):
        mock_get.return_value = _mock_finnhub(_build_payload(20, 120, 60, 180))
        calendar_fetcher.get_events(force_refresh=True)
        data = flask_client.get("/api/impact/symbol?pair=USD/JPY").get_json()
        assert "Non-Farm Payrolls" in data["reason"] or "BLOCKED" in data["reason"]


# ── Scenario 2: ECB Day (EUR/USD cautious, others clear) ──────────────────────

class TestScenarioECBDay:
    @patch("requests.get")
    def test_eurusd_blocked_before_ecb(self, mock_get, flask_client):
        mock_get.return_value = _mock_finnhub(_build_payload(
            nfp_mins=600,  # NFP far away
            ecb_mins=10,   # ECB in 10 minutes
            bh_mins=600,
            mich_past_mins=300
        ))
        calendar_fetcher.get_events(force_refresh=True)
        data = flask_client.get("/api/impact/symbol?pair=EUR/USD").get_json()
        assert data["blocked"] is True
        assert data["confidence_penalty"] == 100

    @patch("requests.get")
    def test_usdjpy_not_blocked_for_ecb_only(self, mock_get, flask_client):
        mock_get.return_value = _mock_finnhub(_build_payload(
            nfp_mins=600,
            ecb_mins=10,
            bh_mins=600,
            mich_past_mins=300
        ))
        calendar_fetcher.get_events(force_refresh=True)
        data = flask_client.get("/api/impact/symbol?pair=USD/JPY").get_json()
        # USD/JPY is not affected by ECB alone
        assert data["blocked"] is False


# ── Scenario 3: Quiet session (no events) ────────────────────────────────────

class TestScenarioQuietSession:
    @patch("requests.get")
    def test_all_pairs_clear_no_events(self, mock_get, flask_client):
        mock_get.return_value = MagicMock()
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = {"economicCalendar": []}
        calendar_fetcher.get_events(force_refresh=True)

        data = flask_client.get("/api/impact/now").get_json()
        for pair_data in data.values():
            assert pair_data["blocked"] is False
            assert pair_data["confidence_penalty"] == 0

    @patch("requests.get")
    def test_upcoming_empty_for_no_events(self, mock_get, flask_client):
        mock_get.return_value = MagicMock()
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = {"economicCalendar": []}
        calendar_fetcher.get_events(force_refresh=True)
        data = flask_client.get("/api/impact/upcoming?hours=24").get_json()
        assert data["total_events"] == 0


# ── Scenario 4: Service degraded (stale cache) ───────────────────────────────

class TestScenarioDegradedService:
    @patch("requests.get")
    def test_still_responds_with_stale_cache(self, mock_get, flask_client):
        import requests
        # First: successful fetch
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"economicCalendar": []}
        mock_get.return_value = ok_resp
        calendar_fetcher.get_events(force_refresh=True)

        # Then FinnHub goes down
        mock_get.side_effect = requests.ConnectionError("down")
        calendar_fetcher._last_refresh = 0  # force retry
        calendar_fetcher.get_events()

        # Health shows degraded
        data = flask_client.get("/api/impact/health").get_json()
        assert data["status"] == "degraded"

        # Symbol endpoint still works
        resp = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        assert resp.status_code == 200

    @patch("requests.get")
    def test_source_stale_when_cache_error(self, mock_get, flask_client):
        import requests
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"economicCalendar": []}
        mock_get.return_value = ok_resp
        calendar_fetcher.get_events(force_refresh=True)

        mock_get.side_effect = requests.ConnectionError("down")
        calendar_fetcher._last_refresh = 0
        calendar_fetcher.get_events()

        data = flask_client.get("/api/impact/symbol?pair=USD/JPY").get_json()
        assert data["source"] == "stale"


# ── Scenario 5: Scalping engine integration flow ──────────────────────────────

class TestScenarioScalpingEngineFlow:
    """
    Simulates the exact call pattern the scalping engine uses each scan cycle:
    1. Call /symbol for each pair
    2. If blocked → skip
    3. If penalty > 0 → raise MIN_CONFIDENCE by penalty
    4. If clear → trade normally
    """

    @patch("requests.get")
    def test_scan_cycle_all_clear(self, mock_get, flask_client):
        import requests as req
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"economicCalendar": []}
        mock_get.return_value = ok_resp
        calendar_fetcher.get_events(force_refresh=True)

        import pair_mapper
        for sym in sorted(pair_mapper.ACTIVE_PAIRS):
            data = flask_client.get(f"/api/impact/symbol?pair={sym}").get_json()
            assert data["blocked"] is False
            assert data["confidence_penalty"] == 0
            # Engine logic: trade normally

    @patch("requests.get")
    def test_scan_cycle_nfp_blocks_all(self, mock_get, flask_client):
        mock_get.return_value = _mock_finnhub(_build_payload(15, 300, 300, 300))
        calendar_fetcher.get_events(force_refresh=True)

        import pair_mapper
        skipped = []
        for sym in sorted(pair_mapper.ACTIVE_PAIRS):
            data = flask_client.get(f"/api/impact/symbol?pair={sym}").get_json()
            if data["blocked"]:
                skipped.append(sym)
        # All USD pairs should be skipped
        assert len(skipped) == len(pair_mapper.ACTIVE_PAIRS)

    @patch("requests.get")
    def test_scan_cycle_moderate_event_raises_threshold(self, mock_get, flask_client):
        # Retail Sales (impact 8, penalty 25) in 20 minutes
        payload = {
            "economicCalendar": [{
                "country": "US", "event": "Retail Sales MoM", "impact": "high",
                "time": _future(20),
                "actual": None, "estimate": "0.4", "prev": "0.3", "unit": "%",
            }]
        }
        mock_get.return_value = _mock_finnhub(payload)
        calendar_fetcher.get_events(force_refresh=True)

        data = flask_client.get("/api/impact/symbol?pair=USD/JPY").get_json()
        # Should not be fully blocked (impact 8 → penalty 25, not 100)
        assert data["blocked"] is False
        assert data["confidence_penalty"] == 25
        # Engine logic: MIN_CONFIDENCE += 25

    @patch("requests.get")
    def test_full_pipeline_response_time(self, mock_get, flask_client):
        import time
        mock_get.return_value = _mock_finnhub(_build_payload(20, 120, 60, 180))
        calendar_fetcher.get_events(force_refresh=True)

        start = time.time()
        for _ in range(10):
            flask_client.get("/api/impact/symbol?pair=USD/JPY")
        elapsed = time.time() - start
        # 10 requests should complete in under 1 second (no network calls)
        assert elapsed < 1.0, f"10 requests took {elapsed:.2f}s — too slow"


# ── Surprise detection — system-level scenarios ───────────────────────────────

def _build_surprise_payload(minutes_offset, actual, estimate, event="CPI MoM",
                             country="US", impact="high", prev="0.3"):
    """Build a single-event FinnHub payload for surprise testing."""
    now = datetime.now(timezone.utc)
    t   = now + timedelta(minutes=minutes_offset)
    return {"economicCalendar": [{
        "country":  country,
        "event":    event,
        "impact":   impact,
        "time":     t.strftime("%Y-%m-%d %H:%M:%S"),
        "actual":   actual,
        "estimate": estimate,
        "prev":     prev,
        "unit":     "%",
    }]}


class TestScenarioSurpriseEvent:
    @patch("calendar_fetcher.requests.get")
    def test_inline_cpi_blocked_during_window(self, mock_get, flask_client):
        """CPI that matches estimate: normal window active, pair blocked."""
        mock_get.return_value = _mock_finnhub(_build_surprise_payload(-10, "0.3", "0.3"))
        calendar_fetcher.get_events(force_refresh=True)
        rv = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        data = rv.get_json()
        assert data["blocked"] is True
        assert data["active_events"][0]["surprise_level"] == "none"
        assert data["active_events"][0]["extra_post_mins"] == 0

    @patch("calendar_fetcher.requests.get")
    def test_extreme_surprise_cpi_blocked_beyond_normal_window(self, mock_get, flask_client):
        """Extreme CPI surprise: pair still blocked 70 min after event (normal +60 extended to +120)."""
        mock_get.return_value = _mock_finnhub(_build_surprise_payload(-70, "0.8", "0.3"))
        calendar_fetcher.get_events(force_refresh=True)
        rv = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        data = rv.get_json()
        assert data["blocked"] is True
        assert data["active_events"][0]["surprise_level"] == "extreme"
        assert data["active_events"][0]["extra_post_mins"] == 60

    @patch("calendar_fetcher.requests.get")
    def test_inline_cpi_unblocked_at_70_min(self, mock_get, flask_client):
        """In-line CPI: pair is clear 70 min after event (past normal +60 window)."""
        mock_get.return_value = _mock_finnhub(_build_surprise_payload(-70, "0.3", "0.3"))
        calendar_fetcher.get_events(force_refresh=True)
        rv = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        data = rv.get_json()
        assert data["blocked"] is False
        assert data["active_events"] == []

    @patch("calendar_fetcher.requests.get")
    def test_notable_surprise_score_escalated(self, mock_get, flask_client):
        """Notable CPI surprise raises impact_level from 9 to 10 via score_boost=1."""
        mock_get.return_value = _mock_finnhub(_build_surprise_payload(-5, "0.4", "0.3"))
        calendar_fetcher.get_events(force_refresh=True)
        rv = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        data = rv.get_json()
        assert data["active_events"]
        ev = data["active_events"][0]
        assert ev["surprise_level"] == "notable"
        assert ev["impact_level"] == 10
        assert ev["base_impact_level"] == 9

    @patch("calendar_fetcher.requests.get")
    def test_pre_event_no_actual_no_surprise(self, mock_get, flask_client):
        """Pre-event CPI (actual=None): normal window, no surprise escalation."""
        mock_get.return_value = _mock_finnhub(_build_surprise_payload(-20, None, "0.3"))
        calendar_fetcher.get_events(force_refresh=True)
        rv = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        data = rv.get_json()
        assert data["blocked"] is True        # within normal -45 min pre-event window
        assert data["active_events"][0]["surprise_level"] == "none"
        assert data["active_events"][0]["impact_level"] == 9   # no boost

    @patch("calendar_fetcher.requests.get")
    def test_nfp_extreme_surprise_capped_at_10(self, mock_get, flask_client):
        """NFP (10) + extreme boost (2) → impact_level stays capped at 10."""
        mock_get.return_value = _mock_finnhub(
            _build_surprise_payload(-10, "500", "175", event="Non-Farm Payrolls")
        )
        calendar_fetcher.get_events(force_refresh=True)
        rv = flask_client.get("/api/impact/symbol?pair=USD/JPY")
        data = rv.get_json()
        assert data["active_events"]
        assert data["active_events"][0]["impact_level"] == 10
        assert data["active_events"][0]["surprise_level"] == "extreme"

    @patch("calendar_fetcher.requests.get")
    def test_surprise_reflected_in_now_endpoint(self, mock_get, flask_client):
        """Surprise data flows through /api/impact/now for all pairs."""
        mock_get.return_value = _mock_finnhub(_build_surprise_payload(-5, "0.8", "0.3"))
        calendar_fetcher.get_events(force_refresh=True)
        rv = flask_client.get("/api/impact/now")
        data = rv.get_json()
        usdjpy = data["USD/JPY"]
        assert usdjpy["blocked"] is True
        assert usdjpy["active_events"][0]["surprise_level"] == "extreme"
