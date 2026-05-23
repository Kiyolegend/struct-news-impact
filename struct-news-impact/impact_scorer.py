"""
Impact Scorer -- processes raw FinnHub events into per-pair impact assessments.

For each active pair, this module:
  1. Finds all events in the cache that affect that pair
  2. Checks whether each event's time window is currently active
  3. Returns the highest active impact + confidence penalty for that pair
  4. Also returns upcoming events (not yet active) for the calendar view
"""

from datetime import datetime, timezone, timedelta
from typing import Optional
import calendar_fetcher
import pair_mapper


def _parse_event_time(time_str: str) -> Optional[datetime]:
    """
    Parse an event time string from FinnHub into a UTC-aware datetime.

    FinnHub returns times in one of these formats:
      "2026-05-22 17:00:00"         -- space-separated, no tz (treat as UTC)
      "2026-05-22T13:30:00+00:00"   -- ISO 8601 with tz offset
      "2026-05-22T13:30:00Z"        -- ISO 8601 with Z suffix

    Returns None if the string is empty or cannot be parsed.
    """
    if not time_str:
        return None

    s = str(time_str).strip()

    # Fast path: strip any tz offset or Z suffix and normalise the separator,
    # then parse as a plain UTC datetime. Covers all common FinnHub formats.
    try:
        dt = datetime.strptime(
            s.split("+")[0].rstrip("Z").replace("T", " "),
            "%Y-%m-%d %H:%M:%S"
        )
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    # Fall back to fromisoformat for anything with explicit tz offset
    try:
        normalised = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalised)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _is_window_active(event_time: datetime, impact_score: int, now: datetime,
                      extra_post_mins: int = 0) -> tuple[bool, int]:
    """
    Check if the time window for an event is currently active.

    extra_post_mins extends the post-event side of the window (used for surprise events).
    Returns (is_active, minutes_away).
    minutes_away is negative if the event is in the past, positive if upcoming.
    """
    before_mins, after_mins = pair_mapper.get_time_window(impact_score)
    after_mins  += extra_post_mins
    window_start = event_time - timedelta(minutes=before_mins)
    window_end   = event_time + timedelta(minutes=after_mins)

    diff_secs    = (event_time - now).total_seconds()
    minutes_away = int(diff_secs / 60)

    return (window_start <= now <= window_end), minutes_away


def _score_event(raw: dict, now: datetime) -> Optional[dict]:
    """
    Score a single raw FinnHub event dict and return a scored event dict,
    or None if the event cannot be parsed or has no time.
    """
    event_name     = raw.get("event", "Unknown Event")
    finnhub_impact = raw.get("impact", "low")
    time_str       = raw.get("time", "")
    country        = raw.get("country", "")

    event_time = _parse_event_time(time_str)
    if event_time is None:
        return None

    impact_score = pair_mapper.get_impact_score(event_name, finnhub_impact)

    actual_val   = raw.get("actual")
    estimate_val = raw.get("estimate")
    surprise_level, extra_post_mins, score_boost = pair_mapper.get_surprise_level(
        actual_val, estimate_val
    )
    effective_score = min(10, impact_score + score_boost)
    penalty         = pair_mapper.get_confidence_penalty(effective_score)

    is_active, minutes_away = _is_window_active(
        event_time, effective_score, now, extra_post_mins
    )

    return {
        "event":             event_name,
        "country":           country,
        "impact_level":      effective_score,
        "base_impact_level": impact_score,
        "surprise_level":    surprise_level,
        "extra_post_mins":   extra_post_mins,
        "finnhub_impact":    finnhub_impact,
        "scheduled_utc":     event_time.strftime("%Y-%m-%d %H:%M UTC"),
        "minutes_away":      minutes_away,
        "window_active":     is_active,
        "confidence_penalty": penalty,
        "actual":            actual_val if actual_val is not None else "",
        "estimate":          estimate_val if estimate_val is not None else "",
        "prev":              raw.get("prev", ""),
        "unit":              raw.get("unit", ""),
    }


def _build_pair_result(pair: str, events: list, now: datetime,
                       data_source: str) -> dict:
    """
    Build the impact result dict for a single pair from a pre-fetched event list.
    Separated from get_pair_impact so get_all_pairs_impact can fetch the cache once.
    """
    active_events   = []
    upcoming_events = []

    for raw in events:
        country        = raw.get("country", "")
        affected_pairs = pair_mapper.get_affected_pairs(country)
        if pair not in affected_pairs:
            continue

        scored = _score_event(raw, now)
        if scored is None:
            continue

        if scored["window_active"]:
            active_events.append(scored)
        elif 0 < scored["minutes_away"] <= 240:
            upcoming_events.append(scored)

    active_events.sort(key=lambda e: e["impact_level"], reverse=True)
    upcoming_events.sort(key=lambda e: e["minutes_away"])

    if active_events:
        top         = active_events[0]
        top_impact  = top["impact_level"]
        top_penalty = top["confidence_penalty"]
        blocked     = top_penalty >= 100
        timing      = "in progress / just released" if top["minutes_away"] <= 0 \
                      else f"{top['minutes_away']} min away"
        reason = (
            f"{'BLOCKED: ' if blocked else ''}"
            f"{top['event']} ({top['country']}) -- "
            f"impact {top_impact}/10 -- {timing}"
        )
    else:
        top_impact  = 0
        top_penalty = 0
        blocked     = False
        reason      = "clear"

    return {
        "pair":               pair,
        "blocked":            blocked,
        "confidence_penalty": top_penalty,
        "impact_level":       top_impact,
        "reason":             reason,
        "active_events":      active_events,
        "upcoming_events":    upcoming_events[:5],
        "source":             data_source,
        "checked_utc":        now.strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


def get_pair_impact(pair: str) -> dict:
    """
    Compute the current impact assessment for a single currency pair.

    Returns a dict with:
      pair              -- the pair queried
      blocked           -- True if confidence_penalty >= 100
      confidence_penalty -- points to ADD to MIN_CONFIDENCE for this pair right now
      impact_level      -- 1-10 score of the highest active event (0 if none)
      reason            -- human-readable reason string
      active_events     -- list of events whose time windows are currently active
      upcoming_events   -- list of events coming up in the next 4 hours (not yet active)
      source            -- "live" or "stale" depending on cache freshness
    """
    now    = datetime.now(timezone.utc)
    events = calendar_fetcher.get_events()
    status = calendar_fetcher.get_status()
    data_source = "stale" if status.get("last_error") else "live"
    return _build_pair_result(pair, events, now, data_source)


def get_all_pairs_impact() -> dict:
    """
    Compute the current impact assessment for all active pairs at once.
    Fetches the cache ONCE and reuses it for all pairs -- more efficient
    than calling get_pair_impact() five times separately.
    """
    now    = datetime.now(timezone.utc)
    events = calendar_fetcher.get_events()   # single cache fetch
    status = calendar_fetcher.get_status()
    data_source = "stale" if status.get("last_error") else "live"

    return {
        pair: _build_pair_result(pair, events, now, data_source)
        for pair in sorted(pair_mapper.ACTIVE_PAIRS)
    }


def get_upcoming_calendar(hours: int = 24) -> list:
    """
    Return all upcoming events (across all active pairs) within the next N hours,
    deduplicated by event name + time, sorted by scheduled time then impact.
    """
    now    = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours)
    events = calendar_fetcher.get_events()

    seen    = set()
    results = []

    for raw in events:
        country        = raw.get("country", "")
        affected_pairs = pair_mapper.get_affected_pairs(country)

        if not affected_pairs:
            continue

        event_name     = raw.get("event", "Unknown Event")
        finnhub_impact = raw.get("impact", "low")
        time_str       = raw.get("time", "")

        event_time = _parse_event_time(time_str)
        if event_time is None or event_time < now or event_time > cutoff:
            continue

        impact_score            = pair_mapper.get_impact_score(event_name, finnhub_impact)
        penalty                 = pair_mapper.get_confidence_penalty(impact_score)
        before_mins, after_mins = pair_mapper.get_time_window(impact_score)

        minutes_away = int((event_time - now).total_seconds() / 60)
        dedup_key    = f"{event_name}|{time_str}|{country}"

        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        results.append({
            "event":             event_name,
            "country":           country,
            "impact_level":      impact_score,
            "finnhub_impact":    finnhub_impact,
            "scheduled_utc":     event_time.strftime("%Y-%m-%d %H:%M UTC"),
            "minutes_away":      minutes_away,
            "confidence_penalty": penalty,
            "block_window":      f"-{before_mins}min to +{after_mins}min",
            "affects_pairs":     affected_pairs,
            "actual":            raw.get("actual", ""),
            "estimate":          raw.get("estimate", ""),
            "prev":              raw.get("prev", ""),
            "unit":              raw.get("unit", ""),
        })

    results.sort(key=lambda e: (e["minutes_away"], -e["impact_level"]))
    return results
