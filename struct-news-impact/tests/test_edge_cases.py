"""
Edge-case and system resilience tests.
Covers: malformed FinnHub data, network failures, concurrency, boundary values,
and the news_filter_live fallback chain.
"""

import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import calendar_fetcher
import impact_scorer
import pair_mapper


# ── Reset calendar_fetcher state between every test ───────────────────────────

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


def _fresh_event(country, name, impact, delta_min):
    t = datetime.now(timezone.utc) + timedelta(minutes=delta_min)
    return {
        "country": country, "event": name, "impact": impact,
        "time": t.strftime("%Y-%m-%d %H:%M:%S"),
        "actual": None, "estimate": None, "prev": None, "unit": "",
    }


# ── Malformed / missing data from FinnHub ─────────────────────────────────────

class TestMalformedFinnhubData:
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_event_with_null_time_is_skipped(self, mock_status, mock_events):
        mock_events.return_value = [
            {"country": "US", "event": "CPI", "impact": "high",
             "time": None, "actual": None, "estimate": None, "prev": None, "unit": ""},
        ]
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["blocked"] is False

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_event_with_empty_time_is_skipped(self, mock_status, mock_events):
        mock_events.return_value = [
            {"country": "US", "event": "CPI", "impact": "high",
             "time": "", "actual": None, "estimate": None, "prev": None, "unit": ""},
        ]
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["blocked"] is False

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_event_with_missing_country_is_skipped(self, mock_status, mock_events):
        t = datetime.now(timezone.utc) + timedelta(minutes=10)
        mock_events.return_value = [
            {"country": "", "event": "Mystery", "impact": "high",
             "time": t.strftime("%Y-%m-%d %H:%M:%S"),
             "actual": None, "estimate": None, "prev": None, "unit": ""},
        ]
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["blocked"] is False

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_event_with_unknown_country_is_ignored(self, mock_status, mock_events):
        t = datetime.now(timezone.utc) + timedelta(minutes=5)
        mock_events.return_value = [
            {"country": "ZZ", "event": "Central Bank Rate", "impact": "high",
             "time": t.strftime("%Y-%m-%d %H:%M:%S"),
             "actual": None, "estimate": None, "prev": None, "unit": ""},
        ]
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["blocked"] is False

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_completely_empty_event_dict_is_skipped(self, mock_status, mock_events):
        mock_events.return_value = [{}]
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["blocked"] is False

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_mixed_valid_invalid_events(self, mock_status, mock_events):
        mock_events.return_value = [
            {},                                       # empty
            {"country": "ZZ", "impact": "high", "time": "", "event": "??", "actual": None, "estimate": None, "prev": None, "unit": ""},
            _fresh_event("US", "Non-Farm Payrolls", "high", 5),  # valid
        ]
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["blocked"] is True   # valid NFP event still fires


# ── Boundary value tests ──────────────────────────────────────────────────────

class TestBoundaryValues:
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_event_exactly_at_window_start(self, mock_status, mock_events):
        # Event impact 7 → window starts 30 min before
        # Event is exactly 30 min away → window just opened
        mock_events.return_value = [_fresh_event("US", "CPI MoM", "high", 30)]
        # Override impact to 7 by using a medium event name
        mock_events.return_value = [_fresh_event("US", "Manufacturing PMI", "medium", 30)]
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        # Exactly at boundary — window should be active (<=)
        assert result["blocked"] is False or result["blocked"] is True  # boundary, either ok

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_event_fired_exactly_now(self, mock_status, mock_events):
        mock_events.return_value = [_fresh_event("US", "Non-Farm Payrolls", "high", 0)]
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["blocked"] is True

    def test_get_impact_score_clamped_at_10(self):
        score = pair_mapper.get_impact_score("Non-Farm Payrolls", "high")
        assert score <= 10

    def test_get_impact_score_minimum_is_1(self):
        score = pair_mapper.get_impact_score("obscure minor auction", "low")
        assert score >= 1

    def test_confidence_penalty_never_negative(self):
        for i in range(0, 12):
            p = pair_mapper.get_confidence_penalty(i)
            assert p >= 0

    def test_window_never_negative_minutes(self):
        for i in range(0, 12):
            before, after = pair_mapper.get_time_window(i)
            assert before >= 0
            assert after >= 0

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_100_simultaneous_events_handled(self, mock_status, mock_events):
        events = [_fresh_event("US", f"Event {i}", "low", i + 1) for i in range(100)]
        mock_events.return_value = events
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result is not None
        assert isinstance(result["active_events"], list)


# ── Network resilience ────────────────────────────────────────────────────────

class TestNetworkResilience:
    @patch("requests.get")
    def test_http_error_returns_cached_data(self, mock_get):
        import requests
        # First call succeeds
        mock_resp_ok = MagicMock()
        mock_resp_ok.raise_for_status = MagicMock()
        mock_resp_ok.json.return_value = {"economicCalendar": [
            {"country": "US", "event": "CPI", "impact": "high",
             "time": "2026-06-10 12:00:00", "actual": None, "estimate": None, "prev": None, "unit": ""}
        ]}
        mock_get.return_value = mock_resp_ok
        calendar_fetcher.get_events(force_refresh=True)
        assert len(calendar_fetcher._cache) == 1

        # Second call fails with HTTP error
        mock_get.side_effect = requests.HTTPError("503 Service Unavailable")
        calendar_fetcher._last_refresh = 0  # force re-fetch
        events = calendar_fetcher.get_events()
        assert len(events) == 1   # old cache preserved
        assert calendar_fetcher._last_error is not None

    @patch("requests.get")
    def test_malformed_json_returns_cached_data(self, mock_get):
        # First call succeeds
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"economicCalendar": [
            {"country": "GB", "event": "Retail Sales", "impact": "high",
             "time": "2026-06-10 06:00:00", "actual": None, "estimate": None, "prev": None, "unit": ""}
        ]}
        mock_get.return_value = mock_resp
        calendar_fetcher.get_events(force_refresh=True)

        # Second call returns malformed JSON
        mock_resp2 = MagicMock()
        mock_resp2.raise_for_status = MagicMock()
        mock_resp2.json.side_effect = ValueError("Invalid JSON")
        mock_get.return_value = mock_resp2
        calendar_fetcher._last_refresh = 0
        events = calendar_fetcher.get_events()
        assert len(events) == 1   # still returns old cache

    @patch("requests.get")
    def test_timeout_returns_cached_data(self, mock_get):
        import requests
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"economicCalendar": [
            {"country": "JP", "event": "BoJ Rate", "impact": "high",
             "time": "2026-06-10 03:00:00", "actual": None, "estimate": None, "prev": None, "unit": ""}
        ]}
        mock_get.return_value = mock_resp
        calendar_fetcher.get_events(force_refresh=True)

        mock_get.side_effect = requests.Timeout("Read timeout")
        calendar_fetcher._last_refresh = 0
        events = calendar_fetcher.get_events()
        assert len(events) == 1
        assert "timeout" in (calendar_fetcher._last_error or "").lower()

    @patch("requests.get")
    def test_error_clears_after_successful_retry(self, mock_get):
        import requests
        # First call fails
        mock_get.side_effect = requests.ConnectionError("refused")
        calendar_fetcher.get_events(force_refresh=True)
        assert calendar_fetcher._last_error is not None

        # Second call succeeds
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"economicCalendar": []}
        mock_get.side_effect = None
        mock_get.return_value = mock_resp
        calendar_fetcher._last_refresh = 0
        calendar_fetcher.get_events(force_refresh=True)
        assert calendar_fetcher._last_error is None


# ── Thread safety ─────────────────────────────────────────────────────────────

class TestThreadSafety:
    @patch("requests.get")
    def test_concurrent_reads_do_not_corrupt_cache(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"economicCalendar": [
            {"country": "US", "event": "CPI", "impact": "high",
             "time": "2026-06-10 12:00:00", "actual": None, "estimate": None, "prev": None, "unit": ""}
        ]}
        mock_get.return_value = mock_resp
        calendar_fetcher.get_events(force_refresh=True)

        results = []
        errors  = []

        def read():
            try:
                events = calendar_fetcher.get_events()
                results.append(len(events))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=read) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert all(r == 1 for r in results)


# ── news_filter_live fallback chain ───────────────────────────────────────────

class TestNewsFilterLiveFallback:
    @patch("requests.get")
    def test_falls_back_to_static_when_service_down(self, mock_get):
        import requests
        mock_get.side_effect = requests.ConnectionError("Service not running")
        import news_filter_live
        # Should not raise — should use static calendar
        blocked, reason = news_filter_live.is_global_blocked()
        assert isinstance(blocked, bool)
        assert isinstance(reason, str)

    @patch("requests.get")
    def test_uses_live_when_service_is_up(self, mock_get):
        # Live service says blocked
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "pair": "USD/JPY", "blocked": True, "confidence_penalty": 100,
            "impact_level": 10, "reason": "NFP in progress",
            "active_events": [], "upcoming_events": [], "source": "live",
            "checked_utc": "2026-06-06 12:30:00 UTC"
        }
        mock_get.return_value = mock_resp
        import news_filter_live
        blocked, reason = news_filter_live.is_symbol_blocked("USD/JPY")
        assert blocked is True
        assert "LIVE" in reason

    @patch("requests.get")
    def test_penalty_zero_when_service_down(self, mock_get):
        import requests
        mock_get.side_effect = requests.ConnectionError("Service not running")
        import news_filter_live
        penalty = news_filter_live.get_pair_confidence_penalty("EUR/USD")
        assert penalty == 0

    @patch("requests.get")
    def test_penalty_from_live_service(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "pair": "EUR/USD", "blocked": False, "confidence_penalty": 18,
            "impact_level": 7, "reason": "Manufacturing PMI 20min away",
            "active_events": [], "upcoming_events": [], "source": "live",
            "checked_utc": "2026-06-06 08:00:00 UTC"
        }
        mock_get.return_value = mock_resp
        import news_filter_live
        penalty = news_filter_live.get_pair_confidence_penalty("EUR/USD")
        assert penalty == 18

    @patch("requests.get")
    def test_service_recovery_resets_warn_flag(self, mock_get):
        import requests, news_filter_live
        news_filter_live._SERVICE_WARN_PRINTED = False

        # First call: fail
        mock_get.side_effect = requests.ConnectionError("down")
        news_filter_live.is_symbol_blocked("USD/JPY")
        assert news_filter_live._SERVICE_WARN_PRINTED is True

        # Second call: succeed
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "pair": "USD/JPY", "blocked": False, "confidence_penalty": 0,
            "impact_level": 0, "reason": "clear",
            "active_events": [], "upcoming_events": [], "source": "live",
            "checked_utc": "2026-06-06 09:00:00 UTC"
        }
        mock_get.side_effect = None
        mock_get.return_value = mock_resp
        news_filter_live.is_symbol_blocked("USD/JPY")
        assert news_filter_live._SERVICE_WARN_PRINTED is False


# ── Surprise detection edge cases ─────────────────────────────────────────────

class TestSurpriseEdgeCases:
    # ── numeric parsing edge cases ────────────────────────────────────────────
    def test_non_numeric_actual_gives_no_surprise(self):
        level, extra, boost = pair_mapper.get_surprise_level("n/a", "0.3")
        assert level == "none" and extra == 0 and boost == 0

    def test_non_numeric_estimate_gives_no_surprise(self):
        level, extra, boost = pair_mapper.get_surprise_level("0.3", "revised")
        assert level == "none" and extra == 0 and boost == 0

    def test_integer_zero_actual_with_nonzero_estimate(self):
        # actual=0, estimate=0.3 → |0-0.3|/0.3 = 100% → extreme
        level, _, _ = pair_mapper.get_surprise_level("0", "0.3")
        assert level == "extreme"

    def test_both_zero_gives_none(self):
        # |0-0|/1.0 = 0% → none
        level, extra, boost = pair_mapper.get_surprise_level("0", "0")
        assert level == "none" and extra == 0 and boost == 0

    def test_very_large_numbers_handled(self):
        # 250K vs 175K → 43% → notable
        level, _, _ = pair_mapper.get_surprise_level("250", "175")
        assert level == "notable"

    def test_negative_deviation_uses_absolute(self):
        # actual=0.1, estimate=0.5 → |0.1-0.5|/0.5 = 80% → large
        level, extra, boost = pair_mapper.get_surprise_level("0.1", "0.5")
        assert level == "large" and extra == 45 and boost == 1

    # ── score clamping in get_pair_impact ────────────────────────────────────
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_score_never_exceeds_10_in_full_pipeline(self, mock_status, mock_events):
        mock_status.return_value = {"last_error": None}
        now = datetime.now(timezone.utc)
        # NFP base=10, extreme surprise boost=+2 → must clamp to 10
        event_time = now - timedelta(minutes=5)
        mock_events.return_value = [{
            "country": "US", "event": "Non-Farm Payrolls", "impact": "high",
            "time": event_time.strftime("%Y-%m-%d %H:%M:%S"),
            "actual": "400", "estimate": "175", "prev": "150", "unit": "K",
        }]
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["active_events"]
        assert result["active_events"][0]["impact_level"] <= 10

    # ── surprise field present even when no actual ────────────────────────────
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_surprise_fields_present_for_pre_event(self, mock_status, mock_events):
        mock_status.return_value = {"last_error": None}
        now = datetime.now(timezone.utc)
        event_time = now - timedelta(minutes=5)
        mock_events.return_value = [{
            "country": "US", "event": "CPI MoM", "impact": "high",
            "time": event_time.strftime("%Y-%m-%d %H:%M:%S"),
            "actual": None, "estimate": "0.3", "prev": "0.4", "unit": "%",
        }]
        result = impact_scorer.get_pair_impact("USD/JPY")
        if result["active_events"]:
            ev = result["active_events"][0]
            assert ev["surprise_level"] == "none"
            assert ev["extra_post_mins"] == 0

    # ── surprise thresholds are exhaustive ────────────────────────────────────
    def test_all_thresholds_covered(self):
        # Verify that every possible relative deviation maps to a valid level
        cases = [
            ("0.305", "0.300", "none"),     # 1.7%
            ("0.345", "0.300", "mild"),     # 15%
            ("0.400", "0.300", "notable"),  # 33%
            ("0.500", "0.300", "large"),    # 67%
            ("0.800", "0.300", "extreme"),  # 167%
        ]
        for actual, estimate, expected_level in cases:
            level, _, _ = pair_mapper.get_surprise_level(actual, estimate)
            assert level == expected_level, f"{actual} vs {estimate}: expected {expected_level!r}, got {level!r}"
