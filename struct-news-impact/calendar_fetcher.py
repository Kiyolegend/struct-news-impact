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
  - On fetch failure: exponential backoff (5 -> 10 -> 20 -> 40 -> 60 min max)
  - On first-call failure: returns empty list with a clear error status
  - 404 on next-week URL is treated as normal end-of-week (skipped silently)
  - All elapsed-time calculations use time.monotonic() -- immune to
    system clock changes, NTP jumps, or a corrupted PC clock
"""

import threading
import time
import requests
from datetime import datetime, timezone
from typing import Optional


FF_THIS_WEEK_URL  = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_NEXT_WEEK_URL  = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"

REFRESH_SECS       = 3600   # background refresh every 60 minutes
REQUEST_TIMEOUT    = 10     # seconds per HTTP request
RETRY_COOLDOWN_MIN = 300    # minimum backoff after a failure: 5 minutes
RETRY_COOLDOWN_MAX = 3600   # maximum backoff after repeated failures: 60 minutes

# ForexFactory's CDN requires a browser-like User-Agent header.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

_lock                               = threading.Lock()
_cache: list                        = []
_last_refresh       = 0.0           # monotonic time of last successful fetch (elapsed-time math)
_last_refresh_wall  = 0.0           # real UTC timestamp of last successful fetch (display only)
_last_attempt       = 0.0           # monotonic time of last fetch attempt (success or failure)
_fail_count: int    = 0             # consecutive failure count -- drives exponential backoff
_last_error: Optional[str]              = None
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

    404 on the next-week URL is treated as normal end-of-week and skipped silently.
    429 rate-limit responses are re-raised immediately so _do_refresh can back off.
    NOTE: this function does NOT hold any lock -- it is safe to call concurrently.
    """
    events = []
    for url in (FF_THIS_WEEK_URL, FF_NEXT_WEEK_URL):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 404 and url == FF_NEXT_WEEK_URL:
                # Next-week calendar not yet published -- normal at end of week
                print(f"[CALENDAR] Next-week URL returned 404 -- skipping (end of week)")
                continue
            if resp.status_code == 429:
                raise requests.HTTPError(
                    f"429 Too Many Requests for {url}", response=resp
                )
            resp.raise_for_status()
            raw_events = resp.json()
            if isinstance(raw_events, list):
                for raw in raw_events:
                    normalized = _normalize_ff_event(raw)
                    if normalized is not None:
                        events.append(normalized)
        except requests.HTTPError:
            raise   # let _do_refresh handle it with backoff
    return events


def _do_refresh() -> None:
    """
    Fetch new calendar data WITHOUT holding the lock, then atomically
    swap the cache (lock held only for the brief swap, not the network call).
    Tracks failure count for exponential backoff.
    Uses time.monotonic() for all timing -- unaffected by PC clock corruption.
    """
    global _cache, _last_refresh, _last_refresh_wall, _last_attempt, _last_error, _fail_count

    # Stamp attempt time using monotonic clock (PC clock independent)
    _last_attempt = time.monotonic()

    try:
        events = _fetch_ff()
        with _lock:
            _cache             = events
            _last_refresh      = time.monotonic()  # monotonic: elapsed-time math
            _last_refresh_wall = time.time()        # wall clock: UTC display only
            _last_error        = None
            _fail_count        = 0
        print(f"[CALENDAR] Refreshed: {len(events)} events from ForexFactory")
    except Exception as e:
        with _lock:
            _last_error  = str(e)
            _fail_count += 1
            cached_count = len(_cache)
        print(
            f"[CALENDAR] Fetch failed (attempt #{_fail_count}): {e} "
            f"-- using cached data ({cached_count} events)"
        )


def _background_refresh_loop() -> None:
    """Daemon thread: keeps the cache warm by refreshing every REFRESH_SECS."""
    while True:
        time.sleep(REFRESH_SECS)
        _do_refresh()


def get_events(force_refresh: bool = False) -> list:
    """
    Return the current cached event list.

    If force_refresh=True or the cache is empty and not in backoff cooldown,
    fetches synchronously.  During backoff cooldown, returns stale cache (or
    empty list) immediately -- avoids hammering ForexFactory after failures.
    All cooldown timing uses time.monotonic() (PC clock independent).
    """
    with _lock:
        cache_empty  = len(_cache) == 0
        current_fail = _fail_count
        last_att     = _last_attempt

    # Compute exponential backoff cooldown using monotonic time
    if current_fail > 0 and last_att > 0:
        backoff     = min(
            RETRY_COOLDOWN_MIN * (2 ** (current_fail - 1)),
            RETRY_COOLDOWN_MAX,
        )
        time_since  = time.monotonic() - last_att
        in_cooldown = time_since < backoff
    else:
        in_cooldown = False

    needs_sync = (force_refresh or cache_empty) and not in_cooldown

    if needs_sync:
        _do_refresh()

    with _lock:
        return list(_cache)


def get_status() -> dict:
    """Return cache health information for the /health endpoint."""
    with _lock:
        snap_last_refresh      = _last_refresh
        snap_last_refresh_wall = _last_refresh_wall
        snap_fail_count        = _fail_count
        snap_last_att          = _last_attempt
        snap_cache_len         = len(_cache)
        snap_last_error        = _last_error

    # Use monotonic clock for all elapsed-time math (PC clock independent)
    age_secs = (
        int(time.monotonic() - snap_last_refresh)
        if snap_last_refresh > 0 else None
    )

    # Next refresh countdown: backoff schedule if failing, normal schedule if healthy
    if snap_fail_count > 0 and snap_last_att > 0:
        backoff      = min(
            RETRY_COOLDOWN_MIN * (2 ** (snap_fail_count - 1)),
            RETRY_COOLDOWN_MAX,
        )
        elapsed      = time.monotonic() - snap_last_att
        next_refresh = max(0, int(backoff - elapsed))
    elif age_secs is not None:
        next_refresh = max(0, REFRESH_SECS - age_secs)
    else:
        next_refresh = 0

    return {
        "events_cached":     snap_cache_len,
        "last_refresh_utc":  (
            datetime.fromtimestamp(snap_last_refresh_wall, tz=timezone.utc)
                    .strftime("%Y-%m-%d %H:%M:%S UTC")
            if snap_last_refresh_wall > 0 else None
        ),
        "cache_age_secs":    age_secs,
        "next_refresh_secs": next_refresh,
        "last_error":        snap_last_error,
        "api_key_set":       True,   # no API key needed -- ForexFactory is free
    }