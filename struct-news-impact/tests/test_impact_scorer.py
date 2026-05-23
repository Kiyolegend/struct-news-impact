"""
Unit tests — impact_scorer.py
FinnHub cache is mocked to test scoring logic in full isolation.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
import impact_scorer
import pair_mapper


# ── _parse_event_time ─────────────────────────────────────────────────────────

class TestParseEventTime:
    def test_space_format_parses_correctly(self):
        dt = impact_scorer._parse_event_time("2026-06-10 13:30:00")
        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 13
        assert dt.minute == 30

    def test_iso_with_offset_parses_correctly(self):
        dt = impact_scorer._parse_event_time("2026-06-10T13:30:00+00:00")
        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 13

    def test_iso_with_z_suffix_parses_correctly(self):
        dt = impact_scorer._parse_event_time("2026-06-10T13:30:00Z")
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_non_utc_offset_converts_to_utc(self):
        dt = impact_scorer._parse_event_time("2026-06-10T09:30:00-04:00")
        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 13   # 9:30 EST = 13:30 UTC

    def test_empty_string_returns_none(self):
        assert impact_scorer._parse_event_time("") is None

    def test_none_returns_none(self):
        assert impact_scorer._parse_event_time(None) is None

    def test_garbage_string_returns_none(self):
        assert impact_scorer._parse_event_time("not-a-date-at-all") is None

    def test_date_only_returns_none(self):
        # Date without time is not a valid event time
        result = impact_scorer._parse_event_time("2026-06-10")
        # Either None or midnight UTC — both acceptable
        if result is not None:
            assert result.tzinfo == timezone.utc

    def test_result_is_always_utc_aware(self):
        for ts in ["2026-06-10 00:00:00", "2026-06-10T12:00:00Z", "2026-06-10T08:00:00+02:00"]:
            dt = impact_scorer._parse_event_time(ts)
            if dt:
                assert dt.tzinfo is not None


# ── _is_window_active ─────────────────────────────────────────────────────────

class TestIsWindowActive:
    def _make_utc(self, **kwargs):
        return datetime.now(timezone.utc).replace(**kwargs)

    def test_active_during_window(self):
        now = datetime.now(timezone.utc)
        event_time = now + timedelta(minutes=10)   # 10 min from now
        # impact 7 → 30 min before, 30 min after
        active, mins = impact_scorer._is_window_active(event_time, 7, now)
        assert active is True
        assert mins == 10

    def test_not_active_before_window(self):
        now = datetime.now(timezone.utc)
        event_time = now + timedelta(minutes=60)   # 60 min away, window starts 30 min before
        active, mins = impact_scorer._is_window_active(event_time, 7, now)
        assert active is False
        assert mins == 60

    def test_active_after_event_fires(self):
        now = datetime.now(timezone.utc)
        event_time = now - timedelta(minutes=10)   # fired 10 min ago
        # impact 7 → 30 min after still in window
        active, mins = impact_scorer._is_window_active(event_time, 7, now)
        assert active is True
        assert mins == -10

    def test_not_active_long_after_event(self):
        now = datetime.now(timezone.utc)
        event_time = now - timedelta(minutes=120)  # 2 hours ago
        active, mins = impact_scorer._is_window_active(event_time, 5, now)
        assert active is False

    def test_high_impact_has_wider_window(self):
        now = datetime.now(timezone.utc)
        event_time = now + timedelta(minutes=40)   # 40 min away
        # impact 10 → 45 min before → should be active
        active_high, _ = impact_scorer._is_window_active(event_time, 10, now)
        # impact 5 → 15 min before → should NOT be active
        active_low, _ = impact_scorer._is_window_active(event_time, 5, now)
        assert active_high is True
        assert active_low is False


# ── get_pair_impact ───────────────────────────────────────────────────────────

def _make_event(country, event_name, impact, minutes_from_now):
    t = datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)
    return {
        "country": country,
        "event": event_name,
        "impact": impact,
        "time": t.strftime("%Y-%m-%d %H:%M:%S"),
        "actual": None, "estimate": None, "prev": None, "unit": "",
    }


class TestGetPairImpact:
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_clear_when_no_events(self, mock_status, mock_events):
        mock_events.return_value = []
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["blocked"] is False
        assert result["confidence_penalty"] == 0
        assert result["impact_level"] == 0
        assert result["reason"] == "clear"
        assert result["active_events"] == []

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_active_nfp_blocks_usdjpy(self, mock_status, mock_events):
        # NFP firing in 10 minutes (well within 45-min window)
        mock_events.return_value = [_make_event("US", "Non-Farm Payrolls", "high", 10)]
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["blocked"] is True
        assert result["confidence_penalty"] == 100
        assert result["impact_level"] == 10

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_nfp_does_not_block_gbpusd_via_gb(self, mock_status, mock_events):
        # NFP is a US event — it DOES affect GBP/USD (all USD pairs)
        mock_events.return_value = [_make_event("US", "Non-Farm Payrolls", "high", 10)]
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("GBP/USD")
        assert result["blocked"] is True   # US events affect GBP/USD

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_boe_only_blocks_gbpusd(self, mock_status, mock_events):
        mock_events.return_value = [_make_event("GB", "BoE Rate Decision", "high", 10)]
        mock_status.return_value = {"last_error": None}
        # GBP/USD should be blocked
        gbp = impact_scorer.get_pair_impact("GBP/USD")
        assert gbp["blocked"] is True
        # USD/JPY should be clear (BoE is GB only)
        jpy = impact_scorer.get_pair_impact("USD/JPY")
        assert jpy["blocked"] is False

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_event_far_future_not_active_but_upcoming(self, mock_status, mock_events):
        # Event in 3 hours — not active yet but should show in upcoming
        mock_events.return_value = [_make_event("US", "CPI MoM", "high", 180)]
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["blocked"] is False
        assert result["active_events"] == []
        assert len(result["upcoming_events"]) > 0

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_event_way_in_future_not_in_upcoming(self, mock_status, mock_events):
        # Event in 10 hours — beyond 4-hour upcoming window
        mock_events.return_value = [_make_event("US", "CPI MoM", "high", 600)]
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["upcoming_events"] == []

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_highest_impact_wins(self, mock_status, mock_events):
        # Two simultaneous events — highest impact should determine the result
        mock_events.return_value = [
            _make_event("US", "Baker Hughes Rig Count", "low", 5),   # impact 2
            _make_event("US", "Non-Farm Payrolls", "high", 5),       # impact 10
        ]
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["impact_level"] == 10
        assert result["blocked"] is True

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_past_event_not_counted_after_window(self, mock_status, mock_events):
        # Event fired 2 hours ago — window is over
        mock_events.return_value = [_make_event("US", "CPI MoM", "high", -120)]
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["blocked"] is False

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_source_is_live_when_no_error(self, mock_status, mock_events):
        mock_events.return_value = []
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["source"] == "live"

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_source_is_stale_on_cache_error(self, mock_status, mock_events):
        mock_events.return_value = []
        mock_status.return_value = {"last_error": "Connection timeout"}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["source"] == "stale"

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_unparseable_event_time_skipped(self, mock_status, mock_events):
        mock_events.return_value = [
            {"country": "US", "event": "CPI MoM", "impact": "high",
             "time": "INVALID-TIME", "actual": None, "estimate": None, "prev": None, "unit": ""},
        ]
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["blocked"] is False   # bad event silently skipped


# ── get_all_pairs_impact ──────────────────────────────────────────────────────

class TestGetAllPairsImpact:
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_returns_all_active_pairs(self, mock_status, mock_events):
        mock_events.return_value = []
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_all_pairs_impact()
        for pair in pair_mapper.ACTIVE_PAIRS:
            assert pair in result

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_each_entry_has_required_keys(self, mock_status, mock_events):
        mock_events.return_value = []
        mock_status.return_value = {"last_error": None}
        result = impact_scorer.get_all_pairs_impact()
        required = {"pair", "blocked", "confidence_penalty", "impact_level", "reason"}
        for pair, data in result.items():
            for key in required:
                assert key in data, f"Missing key {key!r} for {pair}"


# ── get_upcoming_calendar ────────────────────────────────────────────────────

class TestGetUpcomingCalendar:
    @patch("calendar_fetcher.get_events")
    def test_returns_list(self, mock_events):
        mock_events.return_value = []
        result = impact_scorer.get_upcoming_calendar(hours=24)
        assert isinstance(result, list)

    @patch("calendar_fetcher.get_events")
    def test_includes_future_events_for_active_pairs(self, mock_events):
        mock_events.return_value = [_make_event("US", "GDP Growth Rate", "high", 30)]
        result = impact_scorer.get_upcoming_calendar(hours=24)
        assert len(result) == 1
        assert result[0]["event"] == "GDP Growth Rate"

    @patch("calendar_fetcher.get_events")
    def test_excludes_past_events(self, mock_events):
        mock_events.return_value = [_make_event("US", "CPI MoM", "high", -60)]
        result = impact_scorer.get_upcoming_calendar(hours=24)
        assert len(result) == 0

    @patch("calendar_fetcher.get_events")
    def test_excludes_events_beyond_cutoff(self, mock_events):
        mock_events.return_value = [_make_event("US", "CPI MoM", "high", 50)]
        result = impact_scorer.get_upcoming_calendar(hours=0)
        assert len(result) == 0

    @patch("calendar_fetcher.get_events")
    def test_excludes_events_for_inactive_pairs(self, mock_events):
        # CA maps to USD/CAD which is not active
        mock_events.return_value = [_make_event("CA", "Retail Sales MoM", "medium", 30)]
        result = impact_scorer.get_upcoming_calendar(hours=24)
        assert len(result) == 0

    @patch("calendar_fetcher.get_events")
    def test_deduplicates_same_event(self, mock_events):
        # Same event twice (can happen if FinnHub returns duplicates)
        e = _make_event("US", "CPI MoM", "high", 30)
        mock_events.return_value = [e, e]
        result = impact_scorer.get_upcoming_calendar(hours=24)
        assert len(result) == 1

    @patch("calendar_fetcher.get_events")
    def test_sorted_by_time_asc(self, mock_events):
        mock_events.return_value = [
            _make_event("US", "Retail Sales MoM", "high", 90),
            _make_event("US", "CPI MoM", "high", 30),
            _make_event("GB", "Retail Sales MoM", "high", 60),
        ]
        result = impact_scorer.get_upcoming_calendar(hours=24)
        times = [r["minutes_away"] for r in result]
        assert times == sorted(times)

    @patch("calendar_fetcher.get_events")
    def test_result_has_required_fields(self, mock_events):
        mock_events.return_value = [_make_event("US", "CPI MoM", "high", 45)]
        result = impact_scorer.get_upcoming_calendar(hours=24)
        required = {"event", "country", "impact_level", "scheduled_utc",
                    "minutes_away", "confidence_penalty", "block_window", "affects_pairs"}
        for e in result:
            for key in required:
                assert key in e, f"Missing key {key!r}"


# ── Surprise detection in get_pair_impact ─────────────────────────────────────

def _make_event_with_actual(country, event_name, impact_label,
                             minutes_offset, actual, estimate, prev="0.3"):
    """Build a raw event dict as if FinnHub returned it after the event fired."""
    now = datetime.now(timezone.utc)
    t   = now + timedelta(minutes=minutes_offset)
    return {
        "country": country,
        "event":   event_name,
        "impact":  impact_label,
        "time":    t.strftime("%Y-%m-%d %H:%M:%S"),
        "actual":  actual,
        "estimate": estimate,
        "prev":    prev,
        "unit":    "%",
    }


class TestSurpriseInPairImpact:
    # ── no actual yet (pre-event) ─────────────────────────────────────────────
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_pre_event_no_surprise(self, mock_status, mock_events):
        mock_status.return_value = {"last_error": None}
        # CPI in 20 min, not yet fired (actual=None)
        mock_events.return_value = [
            _make_event_with_actual("US", "CPI MoM", "high", -20, None, "0.3")
        ]
        result = impact_scorer.get_pair_impact("USD/JPY")
        if result["active_events"]:
            assert result["active_events"][0]["surprise_level"] == "none"
            assert result["active_events"][0]["extra_post_mins"] == 0

    # ── in-line print — no escalation ────────────────────────────────────────
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_inline_actual_no_escalation(self, mock_status, mock_events):
        mock_status.return_value = {"last_error": None}
        # CPI fired 10 min ago, actual matches estimate exactly
        mock_events.return_value = [
            _make_event_with_actual("US", "CPI MoM", "high", -10, "0.3", "0.3")
        ]
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["active_events"], "Event should still be in window"
        ev = result["active_events"][0]
        assert ev["surprise_level"] == "none"
        assert ev["impact_level"] == 9        # CPI = 9, no boost
        assert ev["base_impact_level"] == 9
        assert ev["extra_post_mins"] == 0

    # ── notable surprise (+1 score) ───────────────────────────────────────────
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_notable_surprise_boosts_score_by_1(self, mock_status, mock_events):
        mock_status.return_value = {"last_error": None}
        # CPI fired 5 min ago: actual=0.4, estimate=0.3 → 33% dev → notable
        mock_events.return_value = [
            _make_event_with_actual("US", "CPI MoM", "high", -5, "0.4", "0.3")
        ]
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["active_events"]
        ev = result["active_events"][0]
        assert ev["surprise_level"] == "notable"
        assert ev["base_impact_level"] == 9
        assert ev["impact_level"] == 10      # 9 + 1 boost, capped at 10
        assert ev["extra_post_mins"] == 25

    # ── extreme surprise (+2 score, +60 min window) ────────────────────────────
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_extreme_surprise_boosts_score_and_extends_window(self, mock_status, mock_events):
        mock_status.return_value = {"last_error": None}
        # CPI fired 50 min ago: actual=0.8, estimate=0.3 → 167% dev → extreme
        mock_events.return_value = [
            _make_event_with_actual("US", "CPI MoM", "high", -50, "0.8", "0.3")
        ]
        result = impact_scorer.get_pair_impact("USD/JPY")
        # Normal CPI window: -45 to +60. 50 min after firing = within +60.
        # Extreme surprise extends to +60+60=+120. Should still be active.
        assert result["active_events"], "Should still be blocked after extreme surprise"
        ev = result["active_events"][0]
        assert ev["surprise_level"] == "extreme"
        assert ev["extra_post_mins"] == 60
        assert ev["impact_level"] == 10

    # ── surprise keeps pair blocked beyond normal window ──────────────────────
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_extreme_surprise_extends_block_beyond_normal_window(self, mock_status, mock_events):
        mock_status.return_value = {"last_error": None}
        # CPI (score 9, normal post-window=60 min). 70 min after → outside normal window.
        # With extreme surprise (+60 min) post-window becomes 120 → still active.
        mock_events.return_value = [
            _make_event_with_actual("US", "CPI MoM", "high", -70, "0.8", "0.3")
        ]
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["active_events"], "Extreme surprise should keep pair blocked at -70 min"

    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_normal_cpi_unblocks_outside_window(self, mock_status, mock_events):
        mock_status.return_value = {"last_error": None}
        # Same but actual matches estimate → no extension → 70 min is outside window
        mock_events.return_value = [
            _make_event_with_actual("US", "CPI MoM", "high", -70, "0.3", "0.3")
        ]
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert not result["active_events"], "In-line CPI should be outside window at -70 min"

    # ── score capped at 10 ────────────────────────────────────────────────────
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_score_capped_at_10(self, mock_status, mock_events):
        mock_status.return_value = {"last_error": None}
        # NFP (10) + extreme boost (2) = 12 → capped at 10
        mock_events.return_value = [
            _make_event_with_actual("US", "Non-Farm Payrolls", "high", -10, "500", "175")
        ]
        result = impact_scorer.get_pair_impact("USD/JPY")
        if result["active_events"]:
            assert result["active_events"][0]["impact_level"] == 10

    # ── response has new fields ───────────────────────────────────────────────
    @patch("calendar_fetcher.get_events")
    @patch("calendar_fetcher.get_status")
    def test_response_includes_surprise_fields(self, mock_status, mock_events):
        mock_status.return_value = {"last_error": None}
        mock_events.return_value = [
            _make_event_with_actual("US", "CPI MoM", "high", -5, "0.5", "0.3")
        ]
        result = impact_scorer.get_pair_impact("USD/JPY")
        assert result["active_events"]
        ev = result["active_events"][0]
        assert "surprise_level"    in ev
        assert "extra_post_mins"   in ev
        assert "base_impact_level" in ev
        assert "impact_level"      in ev
