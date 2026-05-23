"""
Unit tests — pair_mapper.py
Tests every scoring function, mapping, and lookup in isolation.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pair_mapper


# ── get_impact_score ──────────────────────────────────────────────────────────

class TestGetImpactScore:
    def test_nfp_scores_10(self):
        assert pair_mapper.get_impact_score("Non-Farm Payrolls", "high") == 10

    def test_nfp_lowercase(self):
        assert pair_mapper.get_impact_score("non-farm payroll change", "medium") == 10

    def test_fed_rate_decision_scores_10(self):
        assert pair_mapper.get_impact_score("Fed Rate Decision", "high") == 10

    def test_fomc_scores_10(self):
        assert pair_mapper.get_impact_score("FOMC Rate Decision", "high") == 10

    def test_interest_rate_decision_scores_10(self):
        assert pair_mapper.get_impact_score("Interest Rate Decision", "high") == 10

    def test_cpi_scores_9(self):
        assert pair_mapper.get_impact_score("CPI MoM", "high") == 9

    def test_core_cpi_scores_9(self):
        assert pair_mapper.get_impact_score("Core CPI YoY", "high") == 9

    def test_gdp_scores_9(self):
        assert pair_mapper.get_impact_score("GDP Growth Rate QoQ", "high") == 9

    def test_pce_scores_9(self):
        assert pair_mapper.get_impact_score("PCE Price Index MoM", "high") == 9

    def test_retail_sales_scores_8(self):
        assert pair_mapper.get_impact_score("Retail Sales MoM", "high") == 8

    def test_ism_manufacturing_scores_8(self):
        assert pair_mapper.get_impact_score("ISM Manufacturing PMI", "high") == 8

    def test_manufacturing_pmi_scores_7(self):
        assert pair_mapper.get_impact_score("Manufacturing PMI", "medium") == 7

    def test_jobless_claims_scores_6(self):
        assert pair_mapper.get_impact_score("Initial Jobless Claims", "medium") == 6

    def test_speech_scores_3(self):
        assert pair_mapper.get_impact_score("Fed Chair Speech", "medium") == 3

    def test_unknown_high_falls_back_to_8(self):
        assert pair_mapper.get_impact_score("Mystery Event XYZ", "high") == 8

    def test_unknown_medium_falls_back_to_5(self):
        assert pair_mapper.get_impact_score("Mystery Event XYZ", "medium") == 5

    def test_unknown_low_falls_back_to_2(self):
        assert pair_mapper.get_impact_score("Mystery Event XYZ", "low") == 2

    def test_unknown_impact_label_falls_back_to_3(self):
        assert pair_mapper.get_impact_score("Mystery Event XYZ", "unknown") == 3

    def test_empty_event_name(self):
        assert pair_mapper.get_impact_score("", "high") == 8

    def test_none_event_name(self):
        assert pair_mapper.get_impact_score(None, "high") == 8

    def test_case_insensitive_matching(self):
        assert pair_mapper.get_impact_score("CONSUMER PRICE INDEX", "low") == 9

    def test_partial_match_works(self):
        # "cpi" is a substring of "core CPI release"
        assert pair_mapper.get_impact_score("Core CPI Release", "medium") == 9

    def test_boe_rate_scores_10(self):
        assert pair_mapper.get_impact_score("BoE Rate Decision", "high") == 10

    def test_ecb_rate_scores_10(self):
        assert pair_mapper.get_impact_score("ECB Rate Decision", "high") == 10


# ── get_affected_pairs ────────────────────────────────────────────────────────

class TestGetAffectedPairs:
    def test_us_events_affect_all_active_pairs(self):
        pairs = pair_mapper.get_affected_pairs("US")
        assert "USD/JPY" in pairs
        assert "EUR/USD" in pairs
        assert "GBP/USD" in pairs
        assert "AUD/USD" in pairs
        assert "USD/CHF" in pairs

    def test_gb_events_affect_only_gbpusd(self):
        pairs = pair_mapper.get_affected_pairs("GB")
        assert pairs == ["GBP/USD"]

    def test_eu_events_affect_eurusd(self):
        pairs = pair_mapper.get_affected_pairs("EU")
        assert "EUR/USD" in pairs

    def test_de_events_affect_eurusd(self):
        pairs = pair_mapper.get_affected_pairs("DE")
        assert "EUR/USD" in pairs

    def test_jp_events_affect_usdjpy(self):
        pairs = pair_mapper.get_affected_pairs("JP")
        assert "USD/JPY" in pairs

    def test_au_events_affect_audusd(self):
        pairs = pair_mapper.get_affected_pairs("AU")
        assert "AUD/USD" in pairs

    def test_ch_events_affect_usdchf(self):
        pairs = pair_mapper.get_affected_pairs("CH")
        assert "USD/CHF" in pairs

    def test_unknown_country_returns_empty(self):
        pairs = pair_mapper.get_affected_pairs("ZZ")
        assert pairs == []

    def test_empty_country_returns_empty(self):
        pairs = pair_mapper.get_affected_pairs("")
        assert pairs == []

    def test_none_country_returns_empty(self):
        pairs = pair_mapper.get_affected_pairs(None)
        assert pairs == []

    def test_lowercase_country_still_works(self):
        pairs = pair_mapper.get_affected_pairs("us")
        assert "USD/JPY" in pairs

    def test_all_returned_pairs_are_active(self):
        for country in pair_mapper.COUNTRY_PAIR_MAP:
            for p in pair_mapper.get_affected_pairs(country):
                assert p in pair_mapper.ACTIVE_PAIRS, f"{p} from {country} not in ACTIVE_PAIRS"

    def test_ca_usdcad_not_active(self):
        # USD/CAD not in ACTIVE_PAIRS so CA events return empty (no active pairs)
        pairs = pair_mapper.get_affected_pairs("CA")
        assert "USD/CAD" not in pairs


# ── get_time_window ───────────────────────────────────────────────────────────

class TestGetTimeWindow:
    def test_impact_10_has_longest_window(self):
        before, after = pair_mapper.get_time_window(10)
        assert before >= 30
        assert after >= 30

    def test_impact_1_has_shortest_window(self):
        b1, a1 = pair_mapper.get_time_window(1)
        b10, a10 = pair_mapper.get_time_window(10)
        assert b1 <= b10
        assert a1 <= a10

    def test_windows_increase_with_impact(self):
        for i in range(1, 10):
            b_low, a_low = pair_mapper.get_time_window(i)
            b_high, a_high = pair_mapper.get_time_window(i + 1)
            assert b_low <= b_high
            assert a_low <= a_high

    def test_clamps_below_1(self):
        # Should not raise, clamps to 1
        result = pair_mapper.get_time_window(0)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_clamps_above_10(self):
        result = pair_mapper.get_time_window(15)
        assert isinstance(result, tuple)

    def test_returns_tuple_of_two_ints(self):
        before, after = pair_mapper.get_time_window(5)
        assert isinstance(before, int)
        assert isinstance(after, int)


# ── get_confidence_penalty ───────────────────────────────────────────────────

class TestGetConfidencePenalty:
    def test_impact_10_returns_100(self):
        assert pair_mapper.get_confidence_penalty(10) == 100

    def test_impact_9_returns_100(self):
        assert pair_mapper.get_confidence_penalty(9) == 100

    def test_impact_8_returns_25(self):
        assert pair_mapper.get_confidence_penalty(8) == 25

    def test_impact_1_returns_low_value(self):
        assert pair_mapper.get_confidence_penalty(1) <= 5

    def test_penalties_non_decreasing(self):
        prev = 0
        for i in range(1, 11):
            p = pair_mapper.get_confidence_penalty(i)
            assert p >= prev
            prev = p

    def test_clamps_below_1(self):
        result = pair_mapper.get_confidence_penalty(0)
        assert isinstance(result, int)

    def test_clamps_above_10(self):
        result = pair_mapper.get_confidence_penalty(99)
        assert isinstance(result, int)


# ── _parse_numeric ────────────────────────────────────────────────────────────

class TestParseNumeric:
    def test_plain_float(self):
        assert pair_mapper._parse_numeric("0.3") == pytest.approx(0.3)

    def test_plain_int(self):
        assert pair_mapper._parse_numeric("175") == pytest.approx(175.0)

    def test_negative(self):
        assert pair_mapper._parse_numeric("-0.1") == pytest.approx(-0.1)

    def test_percent_stripped(self):
        assert pair_mapper._parse_numeric("0.8%") == pytest.approx(0.8)

    def test_k_suffix_stripped(self):
        # K is stripped — relative deviation maths still holds
        assert pair_mapper._parse_numeric("175K") == pytest.approx(175.0)

    def test_m_suffix_stripped(self):
        assert pair_mapper._parse_numeric("2.5M") == pytest.approx(2.5)

    def test_b_suffix_stripped(self):
        assert pair_mapper._parse_numeric("1.2B") == pytest.approx(1.2)

    def test_comma_stripped(self):
        assert pair_mapper._parse_numeric("1,234.5") == pytest.approx(1234.5)

    def test_none_returns_none(self):
        assert pair_mapper._parse_numeric(None) is None

    def test_empty_string_returns_none(self):
        assert pair_mapper._parse_numeric("") is None

    def test_whitespace_only_returns_none(self):
        assert pair_mapper._parse_numeric("   ") is None

    def test_non_numeric_returns_none(self):
        assert pair_mapper._parse_numeric("n/a") is None

    def test_integer_input(self):
        assert pair_mapper._parse_numeric(3) == pytest.approx(3.0)

    def test_float_input(self):
        assert pair_mapper._parse_numeric(0.5) == pytest.approx(0.5)


# ── get_surprise_level ────────────────────────────────────────────────────────

class TestSurpriseLevel:
    # ── pre-event (actual unknown) ────────────────────────────────────────────
    def test_actual_none_returns_none_level(self):
        level, extra, boost = pair_mapper.get_surprise_level(None, "0.3")
        assert level == "none" and extra == 0 and boost == 0

    def test_estimate_none_returns_none_level(self):
        level, extra, boost = pair_mapper.get_surprise_level("0.3", None)
        assert level == "none" and extra == 0 and boost == 0

    def test_both_none_returns_none(self):
        assert pair_mapper.get_surprise_level(None, None) == ("none", 0, 0)

    def test_actual_empty_string_returns_none(self):
        level, extra, boost = pair_mapper.get_surprise_level("", "0.3")
        assert level == "none"

    # ── in-line print (<10% deviation) ───────────────────────────────────────
    def test_exact_match_is_none(self):
        level, extra, boost = pair_mapper.get_surprise_level("0.3", "0.3")
        assert level == "none" and extra == 0 and boost == 0

    def test_tiny_deviation_is_none(self):
        # 0.31 vs 0.30 = 3.3% deviation → "none"
        level, extra, boost = pair_mapper.get_surprise_level("0.31", "0.30")
        assert level == "none"

    # ── mild surprise (10–25% deviation) ─────────────────────────────────────
    def test_15_pct_deviation_is_mild(self):
        # 0.23 vs 0.20 = 15% → mild
        level, extra, boost = pair_mapper.get_surprise_level("0.23", "0.20")
        assert level == "mild" and extra == 10 and boost == 0

    def test_mild_adds_10_mins_no_score_boost(self):
        _, extra, boost = pair_mapper.get_surprise_level("0.23", "0.20")
        assert extra == 10
        assert boost == 0

    # ── notable surprise (25–50% deviation) ──────────────────────────────────
    def test_33_pct_deviation_is_notable(self):
        # 0.4 vs 0.3 = 33% → notable
        level, extra, boost = pair_mapper.get_surprise_level("0.4", "0.3")
        assert level == "notable" and extra == 25 and boost == 1

    # ── large surprise (50–100% deviation) ───────────────────────────────────
    def test_67_pct_deviation_is_large(self):
        # 0.5 vs 0.3 = 67% → large
        level, extra, boost = pair_mapper.get_surprise_level("0.5", "0.3")
        assert level == "large" and extra == 45 and boost == 1

    # ── extreme surprise (>100% deviation) ───────────────────────────────────
    def test_double_estimate_is_extreme(self):
        # 0.8 vs 0.3 = 167% → extreme
        level, extra, boost = pair_mapper.get_surprise_level("0.8", "0.3")
        assert level == "extreme" and extra == 60 and boost == 2

    def test_extreme_adds_60_mins_and_2_score(self):
        _, extra, boost = pair_mapper.get_surprise_level("0.8", "0.3")
        assert extra == 60
        assert boost == 2

    # ── zero estimate edge case ───────────────────────────────────────────────
    def test_zero_estimate_nonzero_actual_uses_unit_denominator(self):
        # Denominator clamps to 1.0, so |0.5 - 0| / 1.0 = 0.5 → large
        level, _, _ = pair_mapper.get_surprise_level("0.5", "0.0")
        assert level in ("large", "extreme", "notable")

    def test_both_zero_is_none(self):
        level, extra, boost = pair_mapper.get_surprise_level("0.0", "0.0")
        assert level == "none" and extra == 0 and boost == 0

    # ── negative values ───────────────────────────────────────────────────────
    def test_negative_values_use_absolute_deviation(self):
        # -0.6 vs -0.3: |(-0.6) - (-0.3)| / 0.3 = 100% → extreme
        level, _, _ = pair_mapper.get_surprise_level("-0.6", "-0.3")
        assert level == "extreme"

    # ── percent suffix handled correctly ─────────────────────────────────────
    def test_percent_suffix_stripped_before_comparison(self):
        # "0.8%" vs "0.3%" → same as 0.8 vs 0.3 → extreme
        level, _, _ = pair_mapper.get_surprise_level("0.8%", "0.3%")
        assert level == "extreme"

    # ── return types ──────────────────────────────────────────────────────────
    def test_return_is_tuple_of_three(self):
        result = pair_mapper.get_surprise_level("0.5", "0.3")
        assert len(result) == 3

    def test_extra_mins_is_int(self):
        _, extra, _ = pair_mapper.get_surprise_level("0.5", "0.3")
        assert isinstance(extra, int)

    def test_boost_is_int(self):
        _, _, boost = pair_mapper.get_surprise_level("0.5", "0.3")
        assert isinstance(boost, int)
