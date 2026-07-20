"""
Pair Mapper — maps economic events to affected currency pairs and impact multipliers.

Each event is associated with a currency code. This module maps currency codes
to the currency pairs they affect, and applies event-name-based multipliers
to fine-tune the impact level.
"""

# ── Which pairs each currency's events primarily affect ───────────────────────
# Keys are ForexFactory currency codes (USD, GBP, EUR, JPY, AUD, CAD, CHF, CNH).
# Ordered by impact strength (most affected first).
COUNTRY_PAIR_MAP: dict[str, list[str]] = {
    "USD": ["USD/JPY", "EUR/USD", "GBP/USD", "AUD/USD", "USD/CHF", "USD/CAD", "XAU/USD", "BTC/USD", "NZD/USD"],
    "GBP": ["GBP/USD", "GBP/JPY"],
    "EUR": ["EUR/USD", "EUR/JPY"],
    "JPY": ["USD/JPY", "EUR/JPY", "GBP/JPY", "AUD/JPY", "CAD/JPY"],
    "AUD": ["AUD/USD", "AUD/JPY"],
    "CAD": ["USD/CAD", "CAD/JPY"],
    "CHF": ["USD/CHF"],
    "NZD": ["NZD/USD"],
    "CNH": ["AUD/USD", "USD/JPY"],
    "CNY": ["AUD/USD", "USD/JPY"],
}

# ── Pairs the scalping engine actively trades ─────────────────────────────────
ACTIVE_PAIRS = {"USD/JPY", "EUR/USD", "GBP/USD", "AUD/USD", "USD/CHF", "XAU/USD", "BTC/USD", "EUR/JPY", "GBP/JPY", "USD/CAD", "NZD/USD", "AUD/JPY", "CAD/JPY"}

# ── Event name keywords → base impact score override (1–10) ──────────────────
# ForexFactory's "high/medium/low" labels are coarse. Known high-impact event
# names get a more precise score so the engine can calibrate correctly.
# Keys are lowercase substrings matched against the event name.
# ORDER MATTERS: first match wins — more specific entries must come before
# generic ones (e.g. "boj press conference" before "press conference").
EVENT_NAME_SCORES: list[tuple[str, int]] = [
    # ── Tier 10 — market-stopping events ──────────────────────────────────────
    ("non-farm employment",       10),   # ForexFactory: "Non-Farm Employment Change" (NFP)
    ("non-farm payroll",          10),   # alternate name used by some sources
    ("nfp",                       10),
    ("fomc statement",            10),   # ForexFactory: "FOMC Statement"
    ("fomc rate",                 10),
    ("fomc decision",             10),
    ("federal funds rate",        10),
    ("interest rate decision",    10),
    ("monetary policy statement", 10),   # ForexFactory: "Monetary Policy Statement" (ECB/BOJ)
    ("refinancing rate",          10),   # ForexFactory: "Main Refinancing Rate" (ECB)
    ("official bank rate",        10),   # ForexFactory: "MPC Official Bank Rate Votes" (BOE)
    ("boe rate",                  10),
    ("bank of england rate",      10),
    ("ecb rate",                  10),
    ("ecb deposit",               10),

    # ── Tier 9 — top-tier data ─────────────────────────────────────────────────
    ("cpi",                        9),
    ("consumer price index",       9),
    ("core cpi",                   9),
    ("pce",                        9),
    ("core pce",                   9),
    ("gdp",                        9),
    ("gross domestic product",     9),
    ("nonfarm",                    9),
    ("unemployment rate",          9),

    # ── Tier 8 — high impact data ─────────────────────────────────────────────
    ("retail sales",               8),
    ("trade balance",              8),
    ("ppi",                        8),
    ("producer price",             8),
    ("employment change",          8),
    ("jobs",                       8),
    ("ism manufacturing",          8),
    ("ism services",               8),
    ("ism non-manufacturing",      8),

    # ── Tier 7 — notable releases ─────────────────────────────────────────────
    ("manufacturing pmi",          7),
    ("services pmi",               7),
    ("composite pmi",              7),
    ("industrial production",      7),
    ("housing starts",             7),
    ("building permits",           7),
    ("durable goods",              7),
    ("consumer confidence",        7),
    ("jolts",                      7),
    ("adp",                        7),
    ("adp employment",             7),

    # ── Tier 6 — medium-high ──────────────────────────────────────────────────
    ("jobless claims",             6),
    ("initial claims",             6),
    ("continuing claims",          6),
    ("existing home sales",        6),
    ("new home sales",             6),
    ("pending home sales",         6),
    ("michigan sentiment",         6),
    ("university of michigan",     6),
    ("business confidence",        6),

    # ── Tier 5 — medium ───────────────────────────────────────────────────────
    ("factory orders",             5),
    ("wholesale inventories",      5),
    ("business inventories",       5),
    ("personal income",            5),
    ("personal spending",          5),
    ("average earnings",           5),
    ("wage",                       5),

    # ── Tier 4 — lower medium ─────────────────────────────────────────────────
    ("pmi flash",                  4),
    ("flash pmi",                  4),
    ("richmond fed",               4),
    ("philly fed",                 4),
    ("empire state",               4),
    ("chicago pmi",                4),
    ("dallas fed",                 4),

    # ── BOJ / Japan — must come BEFORE generic "speech"/"press conference"
    # entries so that "BOJ Governor Speech" scores 9, not 3, and
    # "BOJ Press Conference" scores 8, not 3.
    ("bank of japan rate",        10),
    ("boj rate",                  10),
    ("boj press conference",       8),
    ("tokyo cpi",                  8),
    ("tankan",                     8),
    ("bank of japan",              9),
    ("boj",                        9),

    # ── Tier 3 — minor ───────────────────────────────────────────────────────
    ("speech",                     3),
    ("speaks",                     3),
    ("testimony",                  3),
    ("remarks",                    3),
    ("press conference",           3),

    # ── Tier 2 — very minor ───────────────────────────────────────────────────
    ("auction",                    2),
    ("t-bill",                     2),
    ("bond",                       2),
    ("note",                       2),
]

# ── Impact label → fallback numeric score ─────────────────────────────────────
FINNHUB_IMPACT_FALLBACK: dict[str, int] = {
    "high":   8,
    "medium": 5,
    "low":    2,
}

# ── Impact level → time window (minutes before, minutes after) ───────────────
IMPACT_WINDOWS: dict[int, tuple[int, int]] = {
    10: (45, 60),
    9:  (45, 60),
    8:  (30, 45),
    7:  (30, 30),
    6:  (20, 25),
    5:  (15, 20),
    4:  (10, 15),
    3:  (10, 10),
    2:  (5,  10),
    1:  (5,   5),
}

# ── Impact level → confidence penalty applied to scalping engine ──────────────
# The engine adds this to MIN_CONFIDENCE for the affected pair.
# A penalty of 100 effectively blocks the pair (no strategy can score that high).
IMPACT_PENALTY: dict[int, int] = {
    10: 100,   # full block
    9:  100,   # full block
    8:  25,    # very cautious — raise bar significantly
    7:  18,
    6:  12,
    5:  8,
    4:  5,
    3:  3,
    2:  2,
    1:  1,
}


def get_impact_score(event_name: str, finnhub_impact: str) -> int:
    """
    Determine the numeric impact score (1–10) for an event.

    Priority: event name keyword match → impact label fallback.
    The name match is more precise than the coarse High/Medium/Low labels.
    """
    name_lower = (event_name or "").lower()
    for keyword, score in EVENT_NAME_SCORES:
        if keyword in name_lower:
            return score
    return FINNHUB_IMPACT_FALLBACK.get((finnhub_impact or "").lower(), 3)


def get_affected_pairs(country: str) -> list[str]:
    """Return the list of active pairs affected by events from the given currency."""
    all_pairs = COUNTRY_PAIR_MAP.get((country or "").upper(), [])
    return [p for p in all_pairs if p in ACTIVE_PAIRS]


def get_time_window(impact_score: int) -> tuple[int, int]:
    """Return (minutes_before, minutes_after) for a given impact score."""
    score = max(1, min(10, impact_score))
    return IMPACT_WINDOWS.get(score, (10, 10))


def get_confidence_penalty(impact_score: int) -> int:
    """Return the confidence penalty to apply to MIN_CONFIDENCE for this impact level."""
    score = max(1, min(10, impact_score))
    return IMPACT_PENALTY.get(score, 3)


# ── Surprise detection ────────────────────────────────────────────────────────
SURPRISE_THRESHOLDS: list[tuple[float, str, int, int]] = [
    (1.00, "extreme", 60, 2),
    (0.50, "large",   45, 1),
    (0.25, "notable", 25, 1),
    (0.10, "mild",    10, 0),
    (0.00, "none",     0, 0),
]


def _parse_numeric(value) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    s = str(value).strip().replace(",", "")
    for suffix in ("%", "B", "M", "K"):
        s = s.replace(suffix, "")
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        return None


def get_surprise_level(actual, estimate) -> tuple[str, int, int]:
    """
    Determine how much an event's actual print deviated from the consensus estimate.

    Returns (level, extra_post_mins, score_boost):
      level           — "none" | "mild" | "notable" | "large" | "extreme"
      extra_post_mins — extra minutes added to the post-event block window
      score_boost     — points added to the base impact score (capped externally at 10)
    """
    actual_f   = _parse_numeric(actual)
    estimate_f = _parse_numeric(estimate)

    if actual_f is None or estimate_f is None:
        return "none", 0, 0

    if abs(estimate_f) < 0.5 and abs(actual_f) < 0.5:
        return "none", 0, 0
    denominator  = abs(estimate_f) if abs(estimate_f) > 0.001 else 1.0
    relative_dev = abs(actual_f - estimate_f) / denominator

    for threshold, level, extra_mins, boost in SURPRISE_THRESHOLDS:
        if relative_dev >= threshold:
            return level, extra_mins, boost

    return "none", 0, 0