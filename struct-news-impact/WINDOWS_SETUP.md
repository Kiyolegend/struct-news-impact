# STRUCT.ai News Impact Service — Windows Setup & Deployment Guide

## What this is

A lightweight background service that runs on your Windows machine alongside
the scalping engine. It fetches live economic event data from FinnHub once per
hour and exposes a local REST API on port 5003. The scalping engine calls this
API before each scan to decide whether to raise its minimum confidence threshold
or skip trading entirely.

Surprise detection is built in: if an event's actual print deviates significantly
from the estimate, the post-event block window extends automatically and the
impact score escalates — no manual configuration required.

---

## Prerequisites

- Windows 10 or 11
- Python 3.10+ from https://python.org  
  ✅ On the installer screen, tick **"Add Python to PATH"**
- Your FinnHub API key (free at https://finnhub.io)
- **No admin rights required** for normal install — only `setup_autostart.bat` needs admin (for Task Scheduler)

---

## Files in this package

| File | Purpose |
|---|---|
| `news_impact_server.py` | The Flask server — this is what runs |
| `calendar_fetcher.py` | Pulls and caches data from FinnHub |
| `impact_scorer.py` | Scores events 1–10, applies surprise detection |
| `pair_mapper.py` | Maps countries → pairs, event names → scores |
| `news_filter_live.py` | **Drop into your scalping engine folder** |
| `install.bat` | One-time setup (run this first) |
| `start.bat` | Start with visible console window |
| `start_background.bat` | Start silently in the background |
| `setup_autostart.bat` | Auto-start on Windows login (recommended) |
| `remove_autostart.bat` | Remove the auto-start task |
| `run_tests.bat` | Runs all 245 tests — no internet needed |
| `WINDOWS_SETUP.md` | This guide |
| `tests/` | Full test suite (unit, integration, system, lifecycle) |

---

## Step 1 — First-time setup (run once)

1. Extract the ZIP to a permanent folder, for example:
   ```
   C:\STRUCT\news-impact-service\
   ```
   Use a path with **no spaces** to avoid Windows path issues.

2. Double-click **`install.bat`**

   It will:
   - Create a `venv\` virtual environment inside the folder (no system-wide changes, no admin needed)
   - Install all packages into `venv\` (avoids the `C:\Python312\Scripts\` permission error)
   - Open `.env` in Notepad automatically

3. In Notepad, fill in your key and save:
   ```
   FINNHUB_API_KEY=your_key_here
   NEWS_IMPACT_PORT=5003
   NEWS_IMPACT_URL=http://localhost:5003
   ```

---

## Step 2 — Starting the service

**Option A — Auto-start on login (recommended for daily trading):**

Double-click **`setup_autostart.bat`** — you may need to right-click and choose
"Run as administrator" the first time.

After this, the service starts automatically every time you log into Windows.
No manual steps before running the scalping engine.

To remove auto-start at any time: run `remove_autostart.bat`

---

**Option B — Manual start with console window:**

Double-click **`start.bat`**. Keep the window open alongside the scalping engine.
Use this option while testing or troubleshooting.

---

**Option C — Manual start in background:**

Double-click **`start_background.bat`** — runs silently, no window.
To stop: open Task Manager → Details tab → find `python.exe` → End task.

---

You should see this in the console (Option B):
```
============================================================
  STRUCT.ai News Impact Service
  Port     : 5003
  Pairs    : AUD/USD, EUR/USD, GBP/USD, USD/CHF, USD/JPY
  Refresh  : every 60 minutes
============================================================
  [INIT] Fetching initial calendar from FinnHub...
  [OK]   Loaded 197 events.
```

---

## Step 3 — Verify it's working

Open your browser and go to:
```
http://localhost:5003/api/impact/health
```

You should see:
```json
{
  "status": "ok",
  "events_cached": 197,
  "last_refresh_utc": "2026-05-22 15:48:19 UTC",
  "cache_age_secs": 42,
  "next_refresh_secs": 3558,
  "api_key_set": true,
  "active_pairs": ["AUD/USD", "EUR/USD", "GBP/USD", "USD/CHF", "USD/JPY"]
}
```

Test a specific pair:
```
http://localhost:5003/api/impact/symbol?pair=USD/JPY
```

---

## Step 4 — Connect to the scalping engine

### What changed from the previous version

Two source files were updated (`pair_mapper.py` and `impact_scorer.py`) to add
surprise detection. The API response for `/api/impact/symbol` now includes three
new fields in each event object:

| New field | What it means |
|---|---|
| `surprise_level` | `"none"` / `"mild"` / `"notable"` / `"large"` / `"extreme"` |
| `base_impact_level` | Original impact score before any surprise boost |
| `extra_post_mins` | Extra minutes added to the post-event window |

The `news_filter_live.py` interface is **unchanged** — `is_global_blocked()`,
`is_symbol_blocked()`, and `get_pair_confidence_penalty()` work exactly the same.
No changes needed in the scalping engine if you already connected it previously.

---

### Connecting for the first time

**Step 4a — Copy the drop-in file**

Copy `news_filter_live.py` from this folder into your scalping engine folder —
the same folder that contains `news_filter.py` and `dashboard_server.py`.

**Step 4b — Change one import line**

In `dashboard_server.py`, change:
```python
from news_filter import is_global_blocked, is_symbol_blocked
```
to:
```python
from news_filter_live import is_global_blocked, is_symbol_blocked
```

That's it. The engine now calls the live service before each scan cycle.
If the service is not running, it silently falls back to the static hardcoded dates.

---

### Optional: dynamic confidence penalty per pair

Add this to your scan loop for finer control:

```python
from news_filter_live import get_pair_confidence_penalty

# Before scoring signals for a pair:
penalty = get_pair_confidence_penalty(symbol)     # 0, 3, 8, 12, 18, 25, or 100
effective_min = config.MIN_CONFIDENCE + penalty   # raise the bar dynamically

# Only accept signals that clear the raised bar:
valid_signals = [s for s in signals if s["score"] >= effective_min]
```

With surprise detection active:
- CPI prints in-line (within 10%) → `penalty = 100` during window, normal window length
- CPI extreme surprise (>100% miss) → `penalty = 100`, window extends by +60 min
- NFP extreme surprise → `penalty = 100`, window extends by +60 min (same — already capped)
- Mid-tier event, notable surprise → `penalty` escalates by one tier

---

## Start order

**Always start in this order:**

1. News Impact Service — wait for `[OK] Loaded N events`
2. Start the scalping engine as normal

With auto-start configured, step 1 happens automatically on login.
If the service isn't running, the engine falls back to static dates — no crash.

---

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/impact/health` | GET | Service health + cache freshness |
| `/api/impact/symbol?pair=USD/JPY` | GET | Per-pair impact (called each scan) |
| `/api/impact/now` | GET | All 5 active pairs at once |
| `/api/impact/upcoming?hours=24` | GET | Upcoming events in next N hours (max 72) |
| `/api/impact/refresh` | POST | Force an immediate FinnHub refresh |

### Full response for `/api/impact/symbol?pair=USD/JPY`

```json
{
  "pair": "USD/JPY",
  "blocked": true,
  "confidence_penalty": 100,
  "impact_level": 10,
  "reason": "BLOCKED: CPI MoM (US) — impact 10/10 — in progress / just released",
  "active_events": [
    {
      "event": "CPI MoM",
      "country": "US",
      "impact_level": 10,
      "base_impact_level": 9,
      "surprise_level": "extreme",
      "extra_post_mins": 60,
      "finnhub_impact": "high",
      "scheduled_utc": "2026-06-06 12:30 UTC",
      "minutes_away": -5,
      "window_active": true,
      "confidence_penalty": 100,
      "actual": "0.8",
      "estimate": "0.3",
      "prev": "0.4",
      "unit": "%"
    }
  ],
  "upcoming_events": [],
  "source": "live",
  "checked_utc": "2026-06-06 12:35:22 UTC"
}
```

---

## Impact levels and what they mean

| Impact | Example Events | Penalty | Block Window |
|---|---|---|---|
| 10 | NFP, Fed, BoE, ECB rate decisions | +100 (full block) | -45 to +60 min |
| 9 | CPI, GDP, PCE, Unemployment Rate | +100 (full block) | -45 to +60 min |
| 8 | Retail Sales, PPI, ISM Manufacturing | +25 | -30 to +45 min |
| 7 | PMI, Durable Goods, JOLTS, ADP | +18 | -30 to +30 min |
| 6 | Jobless Claims, Home Sales, Michigan | +12 | -20 to +25 min |
| 5 | Personal Income, Wage data | +8 | -15 to +20 min |
| 4 | Regional Fed indices (Richmond, Philly) | +5 | -10 to +15 min |
| 3 | Speeches, Testimony, Press Conferences | +3 | -10 to +10 min |
| 1–2 | Auctions, minor data | +1–2 | -5 to +10 min |

## Surprise escalation table

| Deviation from estimate | Level | Extra post-event window | Score boost |
|---|---|---|---|
| < 10% | none | +0 min | +0 |
| 10–25% | mild | +10 min | +0 |
| 25–50% | notable | +25 min | +1 |
| 50–100% | large | +45 min | +1 |
| > 100% | extreme | +60 min | +2 |

---

## Running the tests

Double-click **`run_tests.bat`**, or from the command line:

```cmd
cd C:\STRUCT\news-impact-service
venv\Scripts\python -m pytest tests/ -v
```

All 245 tests use mocked FinnHub calls — no API key, no internet required.
Expected result: **245 passed**.

Test coverage:
- `test_pair_mapper.py` — scoring, mapping, surprise detection (unit)
- `test_calendar_fetcher.py` — FinnHub fetch, caching, stale data (unit)
- `test_impact_scorer.py` — per-pair scoring, surprise escalation (unit)
- `test_edge_cases.py` — malformed data, network failures, edge values (unit)
- `test_api_endpoints.py` — all 5 Flask routes (integration)
- `test_system.py` — full pipeline scenarios incl. surprise (system)
- `test_service_lifecycle.py` — startup, restart, concurrent requests, stress (lifecycle)

---

## Troubleshooting

**"Virtual environment not found" / "run install.bat first"**
Delete any existing `venv\` folder and run `install.bat` again.

**"Python not found"**
Reinstall Python from python.org and tick "Add Python to PATH".

**install.bat fails with a permission error**
Make sure you're using the latest `install.bat` — it uses `venv\` instead of system pip.
If the error persists, delete `venv\` and run `install.bat` again.

**setup_autostart.bat fails**
Right-click → "Run as administrator". Task Scheduler registration requires elevation.

**"Port 5003 already in use"**
Change `NEWS_IMPACT_PORT=5003` to `5004` in `.env`, and `NEWS_IMPACT_URL` to match.

**"FINNHUB_API_KEY not set"**
Check `.env` is in the same folder as `news_impact_server.py` and contains:
```
FINNHUB_API_KEY=your_key_here
```
No quotes, no spaces around the `=`.

**Service shows "degraded" in health check**
FinnHub was temporarily unreachable. The service runs on cached data and retries
automatically each hour. Force an immediate retry:
```
POST http://localhost:5003/api/impact/refresh
```

**Scalping engine not picking up live data**
- Check service is running: http://localhost:5003/api/impact/health
- Check `news_filter_live.py` is in the same folder as `dashboard_server.py`
- Check the import was updated in `dashboard_server.py`
- Look for `[NEWS-LIVE]` messages in the engine's console output
