"""
Calendar Fetcher -- pulls the economic calendar from ForexFactory and caches it.

Data source: https://nfs.faireconomy.media/ff_calendar_thisweek.json
             https://nfs.faireconomy.media/ff_calendar_nextweek.json

No API key required. Data is freely available and covers all major currency
pairs (USD, EUR, GBP, JPY, AUD, CAD, CHF) with High/Medium/Low impact ratings,
forecast, previous, and actual values.

Refresh strategy:
  - Full refresh every 60 minutes via background daemon thread
  - Fetches both this week and next week to cover upcoming sessions
  - Thread-safe: the lock is NEVER held during network I/O
    (fetch happens unlocked, then cache is swapped atomically)
  - On fetch failure: returns the last cached data (stale-ok approach)
  - On first-call failure: returns empty list with a clear error status
"""

import threading
import time
import requests
from datetime import datetime, timezone
from typing import Optional


FF_THIS_WEEK_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_NEXT_WEEK_URL = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"

REFRESH_SECS    = 3600          # refresh every 60 minutes
REQUEST_TIMEOUT = 10            # seconds per request

# ForexFactory's CDN requires a browser-like User-Agent header.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

_lock          = threading.Lock()
_cache: list   = []
_last_refresh  = 0.0            # unix timestamp of last successful fetch
_last_error: Optional[str] = None
_bg_thread: Optional[threading.Thread] = None


def init(api_key: str = "") -> None:
    """
    Start the background refresh thread.
    The api_key parameter is accepted for backward compatibility but is not used --
    ForexFactory requires no API key.
    Must be called once before any fetch.
    """
    global _bg_thread

    # Start background daemon thread (only once)
    if _bg_thread is None or not _bg_thread.is_alive():
        _bg_thread = threading.Thread(
            target=_background_refresh_loop,
            name="CalendarRefresh",
            daemon=True,
        )
        _bg_thread.start()


def _normalize_ff_event(raw: dict) -> Optional[dict]:
    """
    Normalize a raw ForexFactory event dict into the internal format
    expected by impact_scorer.py and the rest of the pipeline.

    ForexFactory field layout (actual API response):
      title    -- event name e.g. "Non-Farm Employment Change"
      country  -- currency code e.g. "USD", "EUR", "GBP", "JPY"
      date     -- FULL ISO-8601 datetime with tz offset e.g. "2026-06-10T08:30:00-04:00"
                  (NOT just a date string -- the time is embedded here)
      time     -- always None in the live feed; time is inside the date field
      impact   -- "High", "Medium", "Low", "Holiday" (title-cased)
      forecast -- forecast/estimate string e.g. "0.3%"  (may be "")
      previous -- previous print e.g. "0.2%"           (may be "")
      actual   -- actual print e.g. "0.4%"             (None if not yet released)
    """
    # date contains the full ISO-8601 datetime including timezone offset.
    # Pass it directly to the time field -- _parse_event_time handles ISO-8601.
    date_str = (raw.get("date") or "").strip()
    if not date_str:
        return None

    impact_raw = (raw.get("impact") or "low").lower()
    # Skip pure holiday markers -- they have no time significance for the engine.
    if impact_raw == "holiday":
        return None

    return {
        "event":    (raw.get("title")   or "Unknown Event"),
        "country":  (raw.get("country") or "").upper(),
        "impact":   impact_raw,
        "time":     date_str,
        "actual":   (raw.get("actual")   or ""),
        "estimate": (raw.get("forecast") or ""),
        "prev":     (raw.get("previous") or ""),
        "unit":     "",
    }


def _fetch_ff() -> list:
    """
    Fetch economic calendar events from ForexFactory (this week + next week).
    Returns a list of normalized internal event dicts, or raises on error.
    NOTE: this function does NOT hold any lock -- it is safe to call concurrently.
    """
    events = []
    for url in (FF_THIS_WEEK_URL, FF_NEXT_WEEK_URL):
        resp = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        raw_events = resp.json()
        if isinstance(raw_events, list):
            for raw in raw_events:
                normalized = _normalize_ff_event(raw)
                if normalized is not None:
                    events.append(normalized)
    return events


def _do_refresh() -> None:
    """
    Fetch new calendar data WITHOUT holding the lock, then atomically
    swap the cache (lock held only for the brief swap, not the network call).
    """
    global _cache, _last_refresh, _last_error

    try:
        events = _fetch_ff()
        with _lock:
            _cache        = events
            _last_refresh = time.time()
            _last_error   = None
        print(f"[CALENDAR] Refreshed: {len(events)} events from ForexFactory")
    except Exception as e:
        with _lock:
            _last_error  = str(e)
            cached_count = len(_cache)
        print(f"[CALENDAR] Fetch failed: {e} -- using cached data ({cached_count} events)")


def _background_refresh_loop() -> None:
    """Daemon thread: keeps the cache warm by refreshing every REFRESH_SECS."""
    while True:
        time.sleep(REFRESH_SECS)
        _do_refresh()


def get_events(force_refresh: bool = False) -> list:
    """
    Return the current cached event list.

    If force_refresh=True or the cache is empty, fetches synchronously.
    Otherwise returns the cache immediately.
    """
    with _lock:
        cache_empty = len(_cache) == 0
        needs_sync  = force_refresh or cache_empty

    if needs_sync:
        _do_refresh()

    with _lock:
        return list(_cache)


def get_status() -> dict:
    """Return cache health information for the /health endpoint."""
    with _lock:
        age_secs = int(time.time() - _last_refresh) if _last_refresh > 0 else None
        return {
            "events_cached":    len(_cache),
            "last_refresh_utc": (
                datetime.fromtimestamp(_last_refresh, tz=timezone.utc)
                        .strftime("%Y-%m-%d %H:%M:%S UTC")
                if _last_refresh > 0 else None
            ),
            "cache_age_secs":   age_secs,
            "next_refresh_secs": max(0, REFRESH_SECS - age_secs) if age_secs is not None else 0,
            "last_error":       _last_error,
            "api_key_set":      True,   # no API key needed -- ForexFactory is free
        }