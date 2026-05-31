"""
STRUCT.ai News Impact Service
==============================
A standalone Flask service that provides live economic news impact data
to the STRUCT.ai Scalping Engine.

Endpoints:
  GET /                            -- live trading dashboard (browser)
  GET /api/impact/now              -- current impact for all active pairs
  GET /api/impact/symbol?pair=X    -- current impact for one pair
  GET /api/impact/upcoming?hours=N -- upcoming events within N hours
  GET /api/impact/health           -- service health + cache status
  POST /api/impact/refresh         -- force an immediate calendar refresh

Port: 5003 (scalping engine is 5002, STRUCT.ai API is 5001/8001)

Usage:
  python news_impact_server.py
  (or run start.bat on Windows)

The scalping engine integrates via news_filter_live.py (drop-in replacement
for news_filter.py) which calls this service before each scan cycle.
"""

import os
import sys
from flask import Flask, request, jsonify, Response
from dotenv import load_dotenv

load_dotenv()

import calendar_fetcher
import impact_scorer
import pair_mapper

PORT = int(os.getenv("NEWS_IMPACT_PORT", 5003))

app = Flask(__name__)


# -- Dashboard HTML ------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>STRUCT.ai News Impact</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #0d1117; color: #e6edf3;
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 14px; line-height: 1.5;
  }
  .header {
    background: #161b22; border-bottom: 1px solid #30363d;
    padding: 14px 24px; display: flex; align-items: center; gap: 16px;
  }
  .header h1 { font-size: 17px; font-weight: 600; color: #f0f6fc; letter-spacing: 0.3px; }
  .header .sub { font-size: 12px; color: #8b949e; }
  .status-pill {
    margin-left: auto; padding: 4px 12px; border-radius: 20px;
    font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
  }
  .pill-ok       { background: #1a4a2e; color: #3fb950; border: 1px solid #238636; }
  .pill-degraded { background: #4a2d00; color: #e3b341; border: 1px solid #d29922; }
  .pill-error    { background: #4a0d0d; color: #f85149; border: 1px solid #da3633; }
  .meta-bar {
    background: #161b22; border-bottom: 1px solid #30363d;
    padding: 8px 24px; display: flex; gap: 24px; flex-wrap: wrap;
    font-size: 12px; color: #8b949e;
  }
  .meta-bar span b { color: #c9d1d9; }
  .refresh-btn {
    margin-left: auto; background: #21262d; border: 1px solid #30363d;
    color: #c9d1d9; padding: 4px 14px; border-radius: 6px;
    cursor: pointer; font-size: 12px; transition: background 0.2s;
  }
  .refresh-btn:hover { background: #30363d; }
  .main { padding: 20px 24px; max-width: 1100px; }
  .section-title {
    font-size: 12px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.8px; color: #8b949e; margin-bottom: 10px;
  }
  .pairs-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
    gap: 10px; margin-bottom: 24px;
  }
  .pair-card {
    border-radius: 8px; padding: 14px 16px; border: 1px solid;
    transition: transform 0.15s;
  }
  .pair-card:hover { transform: translateY(-1px); }
  .card-clear   { background: #0d2318; border-color: #238636; color: #3fb950; }
  .card-caution { background: #1e1a00; border-color: #d29922; color: #e3b341; }
  .card-blocked { background: #200d0d; border-color: #da3633; color: #f85149; }
  .pair-name   { font-size: 15px; font-weight: 700; margin-bottom: 4px; }
  .pair-status { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
  .pair-detail { font-size: 11px; color: #8b949e; margin-top: 6px; line-height: 1.6; }
  .pair-detail b { color: #c9d1d9; }
  .pair-reason { font-size: 11px; margin-top: 5px; opacity: 0.85; font-style: italic; word-break: break-word; }
  .events-table { width: 100%; border-collapse: collapse; margin-bottom: 24px; }
  .events-table th {
    text-align: left; padding: 8px 12px;
    background: #161b22; color: #8b949e;
    font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
    border-bottom: 1px solid #30363d;
  }
  .events-table td {
    padding: 9px 12px; border-bottom: 1px solid #21262d;
    font-size: 12px; vertical-align: middle;
  }
  .events-table tr:last-child td { border-bottom: none; }
  .events-table tr:hover td { background: #161b22; }
  .impact-badge {
    display: inline-block; padding: 2px 7px; border-radius: 10px;
    font-size: 11px; font-weight: 700; text-align: center;
  }
  .impact-10, .impact-9 { background: #4a0d0d; color: #f85149; }
  .impact-8,  .impact-7 { background: #2d1a00; color: #e3b341; }
  .impact-6,  .impact-5 { background: #1a2a00; color: #7ee787; }
  .impact-4,  .impact-3, .impact-2, .impact-1 { background: #1a1f2e; color: #79c0ff; }
  .mins-chip { font-size: 11px; color: #8b949e; white-space: nowrap; }
  .mins-soon { color: #f85149; font-weight: 600; }
  .mins-near { color: #e3b341; }
  .pair-tag {
    display: inline-block; background: #21262d; color: #c9d1d9;
    border-radius: 4px; padding: 1px 5px; font-size: 10px; margin: 1px;
  }
  .no-events  { color: #8b949e; font-size: 13px; padding: 16px 0; }
  .last-updated { font-size: 11px; color: #484f58; margin-top: 8px; text-align: right; }
  .error-bar {
    background: #200d0d; border: 1px solid #da3633; border-radius: 6px;
    padding: 10px 14px; margin-bottom: 16px; color: #f85149; font-size: 12px;
  }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>STRUCT.ai News Impact Service</h1>
    <div class="sub">Live economic event monitoring for forex scalping</div>
  </div>
  <span id="statusPill" class="status-pill pill-degraded">Loading...</span>
</div>
<div class="meta-bar">
  <span><b id="eventsCount">--</b> events cached</span>
  <span>Last refresh: <b id="lastRefresh">--</b></span>
  <span>Cache age: <b id="cacheAge">--</b></span>
  <span>Next refresh: <b id="nextRefresh">--</b></span>
  <button class="refresh-btn" onclick="forceRefresh()">Force Refresh</button>
</div>
<div class="main">
  <div id="errorBar" class="error-bar" style="display:none;"></div>
  <div class="section-title">Current Pair Status</div>
  <div class="pairs-grid" id="pairsGrid">
    <div class="pair-card card-clear" style="opacity:0.3"><div class="pair-name">--</div></div>
  </div>
  <div class="section-title">Upcoming Events -- Next 4 Hours</div>
  <div id="upcomingSection"><div class="no-events">Loading...</div></div>
  <div class="last-updated" id="lastUpdated"></div>
</div>
<script>
function fmtSecs(s) {
  if (s == null || s < 0) return '--';
  const m = Math.floor(s / 60), r = s % 60;
  return m === 0 ? r + 's' : m + 'm ' + r + 's';
}
function cardClass(d) {
  if (d.blocked) return 'card-blocked';
  if (d.confidence_penalty >= 10) return 'card-caution';
  return 'card-clear';
}
function statusLabel(d) {
  if (d.blocked) return 'BLOCKED';
  if (d.confidence_penalty >= 10) return 'CAUTION';
  return 'CLEAR';
}
function minsClass(m) {
  if (m <= 20) return 'mins-soon';
  if (m <= 60) return 'mins-near';
  return '';
}
function impactClass(lvl) {
  if (lvl >= 9) return 'impact-10';
  if (lvl >= 7) return 'impact-8';
  if (lvl >= 5) return 'impact-6';
  return 'impact-4';
}
function escHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function buildPairsGrid(nowData) {
  return Object.keys(nowData).sort().map(pair => {
    const d = nowData[pair];
    const cc = cardClass(d), lbl = statusLabel(d);
    const penalty = d.confidence_penalty || 0, impact = d.impact_level || 0;
    let reasonHtml = '';
    if (d.reason && d.reason !== 'clear')
      reasonHtml = '<div class="pair-reason">' + escHtml(d.reason) + '</div>';
    return '<div class="pair-card ' + cc + '">' +
      '<div class="pair-name">' + escHtml(pair) + '</div>' +
      '<div class="pair-status">' + lbl + '</div>' +
      '<div class="pair-detail"><b>Impact:</b> ' + impact + '/10 &nbsp; <b>Penalty:</b> +' + penalty + '</div>' +
      reasonHtml + '</div>';
  }).join('');
}
function buildUpcomingTable(events) {
  if (!events || events.length === 0)
    return '<div class="no-events">No high-impact events in the next 4 hours.</div>';
  const rows = events.map(ev => {
    const mins = ev.minutes_away;
    const minsStr = mins <= 0
      ? '<span class="mins-soon">NOW / ' + Math.abs(mins) + 'm ago</span>'
      : '<span class="' + minsClass(mins) + '">' + mins + 'm</span>';
    const pairs = (ev.affects_pairs || []).map(p =>
      '<span class="pair-tag">' + escHtml(p) + '</span>').join('');
    const est  = ev.estimate ? escHtml(ev.estimate) + (ev.unit ? ' ' + escHtml(ev.unit) : '') : '--';
    const prev = ev.prev     ? escHtml(ev.prev)     + (ev.unit ? ' ' + escHtml(ev.unit) : '') : '--';
    return '<tr>' +
      '<td class="mins-chip">' + minsStr + '</td>' +
      '<td><span class="impact-badge ' + impactClass(ev.impact_level) + '">' + ev.impact_level + '/10</span></td>' +
      '<td><b>' + escHtml(ev.event) + '</b><br>' +
        '<span style="color:#8b949e;font-size:11px;">' + escHtml(ev.country) +
        ' &mdash; ' + (ev.scheduled_utc || '') + '</span></td>' +
      '<td>' + est + '</td><td>' + prev + '</td>' +
      '<td>' + pairs + '</td>' +
      '<td style="color:#8b949e;">' + escHtml(ev.block_window || '--') + '</td></tr>';
  }).join('');
  return '<table class="events-table"><thead><tr>' +
    '<th>Time</th><th>Impact</th><th>Event</th><th>Estimate</th><th>Prev</th><th>Pairs</th><th>Block Window</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table>';
}
async function fetchAll() {
  try {
    const [healthRes, nowRes, upRes] = await Promise.all([
      fetch('/api/impact/health'),
      fetch('/api/impact/now'),
      fetch('/api/impact/upcoming?hours=4'),
    ]);
    const health = await healthRes.json();
    const nowData = await nowRes.json();
    const upData  = await upRes.json();

    const pill = document.getElementById('statusPill');
    if (health.status === 'ok') { pill.textContent = 'OK'; pill.className = 'status-pill pill-ok'; }
    else { pill.textContent = 'DEGRADED'; pill.className = 'status-pill pill-degraded'; }

    document.getElementById('eventsCount').textContent = health.events_cached || 0;
    document.getElementById('lastRefresh').textContent = health.last_refresh_utc || '--';
    document.getElementById('cacheAge').textContent    = fmtSecs(health.cache_age_secs);
    document.getElementById('nextRefresh').textContent = fmtSecs(health.next_refresh_secs);

    const errBar = document.getElementById('errorBar');
    if (health.last_error) {
      errBar.textContent = 'FinnHub error: ' + health.last_error + ' -- running on stale cache';
      errBar.style.display = 'block';
    } else { errBar.style.display = 'none'; }

    document.getElementById('pairsGrid').innerHTML     = buildPairsGrid(nowData);
    document.getElementById('upcomingSection').innerHTML = buildUpcomingTable(upData.events);
    document.getElementById('lastUpdated').textContent =
      'Last updated: ' + new Date().toLocaleTimeString() + ' (auto-refreshes every 10s)';
  } catch(e) {
    const pill = document.getElementById('statusPill');
    pill.textContent = 'ERROR'; pill.className = 'status-pill pill-error';
    document.getElementById('errorBar').textContent = 'Could not reach service: ' + e.message;
    document.getElementById('errorBar').style.display = 'block';
  }
}
async function forceRefresh() {
  try { await fetch('/api/impact/refresh', { method: 'POST' }); await fetchAll(); } catch(e) {}
}
fetchAll();
setInterval(fetchAll, 10000);
</script>
</body>
</html>"""


# -- Startup -------------------------------------------------------------------

def _startup():
    api_key = os.getenv("FINNHUB_API_KEY", "")
    if not api_key:
        print("=" * 60)
        print("  ERROR: FINNHUB_API_KEY not set.")
        print("  Add it to your .env file:")
        print("    FINNHUB_API_KEY=your_key_here")
        print("=" * 60)
        sys.exit(1)

    calendar_fetcher.init(api_key)   # also starts background refresh thread

    print("=" * 60)
    print("  STRUCT.ai News Impact Service")
    print(f"  Port     : {PORT}")
    print(f"  Pairs    : {', '.join(sorted(pair_mapper.ACTIVE_PAIRS))}")
    print(f"  Refresh  : every {calendar_fetcher.REFRESH_SECS // 60} minutes (background thread)")
    print(f"  Dashboard: http://localhost:{PORT}/")
    print("=" * 60)

    print("  [INIT] Fetching initial calendar from FinnHub...")
    calendar_fetcher.get_events(force_refresh=True)
    status = calendar_fetcher.get_status()
    if status["last_error"]:
        print(f"  [WARN] Initial fetch failed: {status['last_error']}")
        print("         Service will retry automatically. Static fallback is still active.")
    else:
        print(f"  [OK]   Loaded {status['events_cached']} events.")
    print()


# -- Routes --------------------------------------------------------------------

@app.route("/", methods=["GET"])
def dashboard():
    """
    Live trading dashboard -- open in browser during a trading session.
    Shows all 5 pairs colour-coded, confidence penalties, active event reasons,
    and upcoming 4-hour calendar. Auto-refreshes every 10 seconds.
    No internet required -- all data served from local cache.
    """
    return Response(DASHBOARD_HTML, mimetype="text/html")


@app.route("/api/impact/health", methods=["GET"])
def health():
    """Service health and cache status."""
    cache_status = calendar_fetcher.get_status()
    service_ok   = (
        cache_status["events_cached"] > 0
        and not cache_status["last_error"]
    )
    return jsonify({
        "status":       "ok" if service_ok else "degraded",
        **cache_status,
        "active_pairs": sorted(pair_mapper.ACTIVE_PAIRS),
        "port":         PORT,
    })


@app.route("/api/impact/now", methods=["GET"])
def impact_now():
    """Current impact for all active pairs. Cache fetched once for all 5 pairs."""
    return jsonify(impact_scorer.get_all_pairs_impact())


@app.route("/api/impact/symbol", methods=["GET"])
def impact_symbol():
    """Current impact for a single pair. Called by the scalping engine each scan."""
    pair = request.args.get("pair", "").upper()
    if not pair:
        return jsonify({"error": "Missing required query param: pair"}), 400
    if pair not in pair_mapper.ACTIVE_PAIRS:
        return jsonify({"error": f"Unknown pair: {pair}", "known": sorted(pair_mapper.ACTIVE_PAIRS)}), 400
    at_ts = request.args.get("at", type=float)
    return jsonify(impact_scorer.get_pair_impact(pair, at_ts=at_ts))


@app.route("/api/impact/upcoming", methods=["GET"])
def impact_upcoming():
    """Upcoming events for the next N hours affecting at least one active pair."""
    try:
        hours = int(request.args.get("hours", 24))
        hours = max(1, min(hours, 168))
    except (ValueError, TypeError):
        hours = 24
    events = impact_scorer.get_upcoming_calendar(hours=hours)
    status = calendar_fetcher.get_status()
    return jsonify({"hours": hours, "total_events": len(events), "events": events, "cache_status": status})


@app.route("/api/impact/refresh", methods=["POST"])
def force_refresh():
    """Force an immediate calendar refresh from FinnHub."""
    print("[CALENDAR] Force refresh requested via API")
    calendar_fetcher.get_events(force_refresh=True)
    status = calendar_fetcher.get_status()
    return jsonify({"ok": not bool(status["last_error"]), "status": status})


# -- Entry point ---------------------------------------------------------------

if __name__ == "__main__":
    _startup()
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
