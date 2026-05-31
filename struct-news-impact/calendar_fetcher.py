"""
Calendar Fetcher -- pulls the economic calendar from FinnHub and caches it.

Refresh strategy:
  - Full refresh every 60 minutes via background daemon thread
  - Fetches 3 days ahead (today + 2) to cover overnight sessions
  - Thread-safe: the lock is NEVER held during network I/O
    (fetch happens unlocked, then cache is swapped atomically)
  - On FinnHub failure: returns the last cached data (stale-ok approach)
  - On first-call failure: returns empty list with a clear error status
"""

import threading
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional


FINNHUB_BASE    = "https://finnhub.io/api/v1"
REFRESH_SECS    = 3600          # refresh every 60 minutes
FETCH_DAYS_FWD  = 7            # fetch today + next 2 days
REQUEST_TIMEOUT = 10            # seconds per FinnHub request

_lock          = threading.Lock()
_cache: list   = []
_last_refresh  = 0.0            # unix timestamp of last successful fetch
_last_error: Optional[str] = None
_api_key: Optional[str]    = None
_bg_thread: Optional[threading.Thread] = None


def init(api_key: str) -> None:
    """
    Set the FinnHub API key and start the background refresh thread.
    Must be called once before any fetch.
    """
    global _api_key, _bg_thread
    _api_key = api_key

    # Start background daemon thread (only once)
    if _bg_thread is None or not _bg_thread.is_alive():
        _bg_thread = threading.Thread(
            target=_background_refresh_loop,
            name="CalendarRefresh",
            daemon=True,   # daemon=True: thread dies automatically when main process exits
        )
        _bg_thread.start()


def _fetch_range(from_date: str, to_date: str) -> list:
    """
    Fetch economic calendar events from FinnHub for the given date range.
    Returns a list of raw event dicts, or raises on error.
    NOTE: this function does NOT hold any lock -- it is safe to call concurrently.
    """
    if not _api_key:
        raise RuntimeError("FinnHub API key not set -- call init(api_key) first")

    url    = f"{FINNHUB_BASE}/calendar/economic"
    params = {"from": from_date, "to": to_date, "token": _api_key}

    resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    data = resp.json()
    return data.get("economicCalendar", [])


def _do_refresh() -> None:
    """
    Fetch new calendar data WITHOUT holding the lock, then atomically
    swap the cache (lock held only for the brief swap, not the network call).

    This means concurrent readers are NEVER blocked during a FinnHub fetch.
    """
    global _cache, _last_refresh, _last_error

    now     = datetime.now(timezone.utc)
    from_dt = now.strftime("%Y-%m-%d")
    to_dt   = (now + timedelta(days=FETCH_DAYS_FWD)).strftime("%Y-%m-%d")

    # Network fetch -- NO lock held here (up to REQUEST_TIMEOUT seconds)
    try:
        events = _fetch_range(from_dt, to_dt)
        # Atomic cache swap -- lock held only for the list assignment
        with _lock:
            _cache        = events
            _last_refresh = time.time()
            _last_error   = None
        print(f"[CALENDAR] Refreshed: {len(events)} events ({from_dt} -> {to_dt})")
    except Exception as e:
        # Read cache length inside the lock to avoid a data race with the
        # background thread that may be swapping _cache concurrently.
        with _lock:
            _last_error  = str(e)
            cached_count = len(_cache)
        print(f"[CALENDAR] Fetch failed: {e} -- using cached data ({cached_count} events)")


def _background_refresh_loop() -> None:
    """
    Daemon thread: keeps the cache warm by refreshing every REFRESH_SECS.
    Sleeps first so startup fetch (from get_events force_refresh=True) runs first.
    """
    while True:
        time.sleep(REFRESH_SECS)
        _do_refresh()


def get_events(force_refresh: bool = False) -> list:
    """
    Return the current cached event list.

    If force_refresh=True or the cache is empty, fetches synchronously
    (blocks until the fetch completes or fails).
    Otherwise returns the cache immediately -- the background thread
    keeps it fresh, so there is no blocking under normal operation.
    """
    with _lock:
        age         = time.time() - _last_refresh
        cache_empty = len(_cache) == 0
        needs_sync  = force_refresh or cache_empty

    if needs_sync:
        _do_refresh()   # outside the lock -- safe, atomic swap internally

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
            "api_key_set":      bool(_api_key),
        }
