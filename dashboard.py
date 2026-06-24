"""
Live monitoring dashboard for the ICT 2022 trading bot.
Includes an MT5 connection panel so you can connect/disconnect
and view live account data directly from the browser.

Usage:
    python dashboard.py
    Then open http://localhost:8080
"""

import csv
import io
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request, send_file

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

app = Flask(__name__)

STATUS_FILE       = Path("status.json")
TRADES_FILE       = Path("journal/trades.json")
BACKTEST_CSV      = Path("backtest_results.csv")
EQUITY_CURVE_FILE = Path("equity_curve.png")

_mt5_lock  = threading.Lock()
_mt5_state = {"connected": False}


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        return {}


def _today_stats(trades: dict) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_trades = [t for t in trades.values() if t.get("time", "").startswith(today)]
    wins   = [t for t in today_trades if t.get("result") == "win"]
    losses = [t for t in today_trades if t.get("result") == "loss"]
    net    = sum(t.get("pnl_points") or 0 for t in today_trades)
    return {
        "total":  len(today_trades),
        "wins":   len(wins),
        "losses": len(losses),
        "net":    round(net, 2),
    }


def _equity_series(trades: dict) -> list:
    closed = sorted(
        [t for t in trades.values() if t.get("pnl_points") is not None],
        key=lambda t: t.get("time", ""),
    )
    equity, running = [], 0.0
    for t in closed:
        running += t["pnl_points"]
        equity.append({"time": t["time"][:16], "value": round(running, 2)})
    return equity


# ---------------------------------------------------------------------------
# MT5 endpoints
# ---------------------------------------------------------------------------

@app.route("/api/mt5/status")
def api_mt5_status():
    if not MT5_AVAILABLE:
        return jsonify({"available": False, "connected": False,
                        "error": "MetaTrader5 package not installed"})
    with _mt5_lock:
        if not _mt5_state.get("connected"):
            return jsonify({"available": True, "connected": False})
        info = mt5.account_info()
        if info is None:
            _mt5_state["connected"] = False
            return jsonify({"available": True, "connected": False})
        positions = mt5.positions_get(symbol=_mt5_state.get("symbol", "US100")) or []
        tick = mt5.symbol_info_tick(_mt5_state.get("symbol", "US100"))
        return jsonify({
            "available":  True,
            "connected":  True,
            "login":      info.login,
            "server":     info.server,
            "company":    info.company,
            "currency":   info.currency,
            "leverage":   info.leverage,
            "balance":    info.balance,
            "equity":     info.equity,
            "profit":     info.profit,
            "margin":     info.margin,
            "free_margin": info.margin_free,
            "open_count": len(positions),
            "price":      tick.ask if tick else None,
        })


@app.route("/api/mt5/connect", methods=["POST"])
def api_mt5_connect():
    if not MT5_AVAILABLE:
        return jsonify({"ok": False, "error": "MetaTrader5 package not installed"}), 400

    data     = request.get_json(force=True) or {}
    login    = data.get("login", "")
    password = data.get("password", "")
    server   = data.get("server", "")
    symbol   = data.get("symbol", "US100")

    if not login or not password or not server:
        return jsonify({"ok": False, "error": "Login, password and server are required"}), 400

    with _mt5_lock:
        mt5.shutdown()
        try:
            login_int = int(login)
        except ValueError:
            return jsonify({"ok": False, "error": "Login must be a number"}), 400

        ok = mt5.initialize(login=login_int, password=password, server=server)
        if not ok:
            _mt5_state["connected"] = False
            err = mt5.last_error()
            return jsonify({"ok": False, "error": f"MT5 error {err[0]}: {err[1]}"}), 400

        info = mt5.account_info()
        if info is None:
            _mt5_state["connected"] = False
            return jsonify({"ok": False, "error": "Connected but could not read account info"}), 400

        _mt5_state.update({"connected": True, "symbol": symbol})
        return jsonify({
            "ok":      True,
            "login":   info.login,
            "server":  info.server,
            "company": info.company,
            "balance": info.balance,
            "equity":  info.equity,
        })


@app.route("/api/mt5/disconnect", methods=["POST"])
def api_mt5_disconnect():
    with _mt5_lock:
        if MT5_AVAILABLE:
            mt5.shutdown()
        _mt5_state["connected"] = False
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Bot API endpoints
# ---------------------------------------------------------------------------

@app.route("/api/status")
def api_status():
    return jsonify(_read_json(STATUS_FILE))


@app.route("/api/trades")
def api_trades():
    trades = _read_json(TRADES_FILE)
    recent = sorted(trades.values(),
                    key=lambda t: t.get("time", ""), reverse=True)[:20]
    return jsonify({
        "recent":  recent,
        "today":   _today_stats(trades),
        "equity":  _equity_series(trades),
        "total":   len(trades),
        "wins":    sum(1 for t in trades.values() if t.get("result") == "win"),
        "losses":  sum(1 for t in trades.values() if t.get("result") == "loss"),
        "open":    sum(1 for t in trades.values() if t.get("result") is None),
        "net_pts": round(sum(t.get("pnl_points") or 0 for t in trades.values()), 2),
    })


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ICT 2022 Bot Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}
header{background:#161b22;border-bottom:1px solid #30363d;padding:14px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
header h1{font-size:1.1rem;color:#e6edf3;font-weight:600}
.badge{padding:3px 10px;border-radius:12px;font-size:.72rem;font-weight:700}
.badge.live{background:#238636;color:#fff}
.badge.dry{background:#1f6feb;color:#fff}
.badge.stopped{background:#6e7681;color:#fff}
.badge.connected{background:#238636;color:#fff}
.badge.disconnected{background:#6e7681;color:#fff}
.pulse{width:8px;height:8px;border-radius:50%;background:#3fb950;animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.updated{font-size:.75rem;color:#8b949e;margin-left:auto}
main{padding:20px 24px;display:flex;flex-direction:column;gap:20px}
.row{display:grid;gap:16px}
.row.cols-4{grid-template-columns:repeat(4,1fr)}
.row.cols-3{grid-template-columns:repeat(3,1fr)}
.row.cols-2{grid-template-columns:repeat(2,1fr)}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:18px}
.card h2{font-size:.78rem;color:#8b949e;text-transform:uppercase;letter-spacing:.06em;margin-bottom:12px}
.stat-val{font-size:1.6rem;font-weight:700;color:#e6edf3}
.stat-val.green{color:#3fb950}.stat-val.red{color:#f85149}.stat-val.blue{color:#58a6ff}
.stat-sub{font-size:.75rem;color:#8b949e;margin-top:3px}
.session-dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
.session-dot.active{background:#3fb950}.session-dot.inactive{background:#6e7681}
table{width:100%;border-collapse:collapse;font-size:.82rem}
th{text-align:left;padding:8px 10px;color:#8b949e;font-weight:500;border-bottom:1px solid #30363d;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em}
td{padding:8px 10px;border-bottom:1px solid #21262d;color:#c9d1d9}
tr:last-child td{border-bottom:none}
.win{color:#3fb950;font-weight:600}.loss{color:#f85149;font-weight:600}
.open-tag{color:#58a6ff;font-weight:600}
.dir-bull{color:#3fb950}.dir-bear{color:#f85149}
.no-data{color:#8b949e;font-size:.82rem;padding:12px 0}
canvas{max-height:220px}

/* MT5 panel */
#mt5-panel{border-color:#30363d}
#mt5-panel.mt5-connected{border-color:#238636}
.mt5-status-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.mt5-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.mt5-dot.on{background:#3fb950;box-shadow:0 0 6px #3fb950}
.mt5-dot.off{background:#6e7681}
.mt5-info-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px 20px;margin-top:12px;font-size:.82rem}
.mt5-info-grid span{color:#8b949e}
.mt5-info-grid strong{color:#e6edf3}
.mt5-form{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px;align-items:flex-end}
.mt5-form label{display:flex;flex-direction:column;gap:4px;font-size:.75rem;color:#8b949e}
.mt5-form input{background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;
  padding:7px 10px;font-size:.82rem;width:160px;outline:none}
.mt5-form input:focus{border-color:#58a6ff}
.mt5-form input.narrow{width:110px}
.btn{padding:7px 16px;border-radius:6px;border:none;cursor:pointer;font-size:.82rem;font-weight:600;transition:opacity .15s}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-connect{background:#238636;color:#fff}
.btn-connect:hover:not(:disabled){background:#2ea043}
.btn-disconnect{background:#6e7681;color:#fff}
.btn-disconnect:hover:not(:disabled){background:#8b949e}
.mt5-error{color:#f85149;font-size:.78rem;margin-top:8px}
.mt5-success{color:#3fb950;font-size:.78rem;margin-top:8px}

@media(max-width:900px){.row.cols-4,.row.cols-3{grid-template-columns:repeat(2,1fr)}}
@media(max-width:560px){.row.cols-4,.row.cols-3,.row.cols-2{grid-template-columns:1fr}.mt5-form input{width:100%}}
</style>
</head>
<body>
<header>
  <div class="pulse" id="pulse"></div>
  <h1>ICT 2022 — US100 Dashboard</h1>
  <span class="badge dry" id="mode-badge">—</span>
  <span id="session-badge" style="font-size:.8rem;color:#8b949e">—</span>
  <a href="/backtest" style="margin-left:12px;padding:4px 14px;border-radius:12px;background:#1f6feb;color:#fff;font-size:.75rem;font-weight:700;text-decoration:none">Backtest</a>
  <span class="updated" id="last-updated">—</span>
</header>

<main>

  <!-- MT5 Connection Panel -->
  <div class="card" id="mt5-panel">
    <h2 style="display:flex;align-items:center;gap:10px">
      MT5 Connection
      <span class="badge disconnected" id="mt5-badge">DISCONNECTED</span>
    </h2>

    <!-- Disconnected: show login form -->
    <div id="mt5-form-wrap">
      <div class="mt5-form">
        <label>Account Login
          <input type="number" id="mt5-login" placeholder="12345678" class="narrow">
        </label>
        <label>Password
          <input type="password" id="mt5-password" placeholder="••••••••">
        </label>
        <label>Server
          <input type="text" id="mt5-server" placeholder="ICMarkets-Demo02">
        </label>
        <label>Symbol
          <input type="text" id="mt5-symbol" placeholder="US100" value="US100" class="narrow">
        </label>
        <button class="btn btn-connect" onclick="connectMT5()" id="mt5-connect-btn">Connect</button>
      </div>
      <div id="mt5-msg"></div>
    </div>

    <!-- Connected: show account info -->
    <div id="mt5-info-wrap" style="display:none">
      <div class="mt5-status-row">
        <div class="mt5-dot on"></div>
        <span id="mt5-company" style="color:#e6edf3;font-weight:600"></span>
        <span id="mt5-server-label" style="color:#8b949e;font-size:.8rem"></span>
        <span id="mt5-login-label" style="color:#8b949e;font-size:.8rem"></span>
        <button class="btn btn-disconnect" onclick="disconnectMT5()" style="margin-left:auto">Disconnect</button>
      </div>
      <div class="mt5-info-grid" id="mt5-account-grid"></div>
    </div>
  </div>

  <!-- Account Stats (from bot status.json or MT5 when connected) -->
  <div class="row cols-4">
    <div class="card"><h2>Balance</h2>
      <div class="stat-val" id="balance">—</div>
      <div class="stat-sub">Account balance</div></div>
    <div class="card"><h2>Equity</h2>
      <div class="stat-val" id="equity">—</div>
      <div class="stat-sub">Current equity</div></div>
    <div class="card"><h2>Open P&amp;L</h2>
      <div class="stat-val" id="open-pnl">—</div>
      <div class="stat-sub">Floating profit</div></div>
    <div class="card"><h2>US100 Price</h2>
      <div class="stat-val blue" id="price">—</div>
      <div class="stat-sub" id="price-sub">—</div></div>
  </div>

  <!-- Today + All Time -->
  <div class="row cols-4">
    <div class="card"><h2>Today — Trades</h2>
      <div class="stat-val" id="today-trades">—</div>
      <div class="stat-sub" id="today-wl">—</div></div>
    <div class="card"><h2>Today — Net Pts</h2>
      <div class="stat-val" id="today-net">—</div>
      <div class="stat-sub">Points today</div></div>
    <div class="card"><h2>All-Time Trades</h2>
      <div class="stat-val" id="total-trades">—</div>
      <div class="stat-sub" id="total-wl">—</div></div>
    <div class="card"><h2>All-Time Net Pts</h2>
      <div class="stat-val" id="total-net">—</div>
      <div class="stat-sub">Cumulative points</div></div>
  </div>

  <!-- Equity Curve + Open Positions -->
  <div class="row cols-2">
    <div class="card">
      <h2>Equity Curve</h2>
      <canvas id="equity-chart"></canvas>
    </div>
    <div class="card">
      <h2>Open Positions</h2>
      <div id="open-positions-wrap">
        <p class="no-data">No open positions</p>
      </div>
    </div>
  </div>

  <!-- Recent Trades -->
  <div class="card">
    <h2>Recent Trades</h2>
    <div id="recent-trades-wrap">
      <p class="no-data">No trades yet</p>
    </div>
  </div>

</main>

<script>
let equityChart = null;
let mt5Connected = false;

function fmt(n, d=2) {
  if (n === null || n === undefined) return '—';
  return Number(n).toLocaleString(undefined, {minimumFractionDigits:d, maximumFractionDigits:d});
}
function colorVal(n) { return (n >= 0) ? 'green' : 'red'; }
function setMsg(text, cls) {
  const el = document.getElementById('mt5-msg');
  el.textContent = text;
  el.className = cls;
}

// ---------------------------------------------------------------------------
// MT5 connection panel
// ---------------------------------------------------------------------------

async function refreshMT5() {
  try {
    const r = await fetch('/api/mt5/status');
    const d = await r.json();

    if (!d.available) {
      document.getElementById('mt5-badge').textContent = 'NOT INSTALLED';
      document.getElementById('mt5-badge').className = 'badge stopped';
      return;
    }

    mt5Connected = d.connected;

    if (d.connected) {
      document.getElementById('mt5-panel').classList.add('mt5-connected');
      document.getElementById('mt5-badge').textContent = 'CONNECTED';
      document.getElementById('mt5-badge').className = 'badge connected';
      document.getElementById('mt5-form-wrap').style.display = 'none';
      document.getElementById('mt5-info-wrap').style.display = 'block';

      document.getElementById('mt5-company').textContent = d.company || '';
      document.getElementById('mt5-server-label').textContent = d.server || '';
      document.getElementById('mt5-login-label').textContent = '#' + (d.login || '');

      const items = [
        ['Balance',     '$' + fmt(d.balance)],
        ['Equity',      '$' + fmt(d.equity)],
        ['Floating P&L','$' + fmt(d.profit)],
        ['Margin',      '$' + fmt(d.margin)],
        ['Free Margin', '$' + fmt(d.free_margin)],
        ['Leverage',    '1:' + (d.leverage || '—')],
        ['Currency',    d.currency || '—'],
        ['Open Trades', d.open_count ?? '—'],
      ];
      document.getElementById('mt5-account-grid').innerHTML =
        items.map(([l,v]) => `<div><span>${l}</span><br><strong>${v}</strong></div>`).join('');

      // Update the main stat cards with live MT5 data
      document.getElementById('balance').textContent = '$' + fmt(d.balance);
      const eq = document.getElementById('equity');
      eq.textContent = '$' + fmt(d.equity);
      const pnl = document.getElementById('open-pnl');
      pnl.textContent = (d.profit >= 0 ? '+' : '') + '$' + fmt(d.profit);
      pnl.className = 'stat-val ' + colorVal(d.profit);
      if (d.price) {
        document.getElementById('price').textContent = fmt(d.price, 0);
        document.getElementById('price-sub').textContent = 'Live bid/ask';
      }
    } else {
      document.getElementById('mt5-panel').classList.remove('mt5-connected');
      document.getElementById('mt5-badge').textContent = 'DISCONNECTED';
      document.getElementById('mt5-badge').className = 'badge disconnected';
      document.getElementById('mt5-form-wrap').style.display = 'block';
      document.getElementById('mt5-info-wrap').style.display = 'none';
    }
  } catch(e) { console.error('MT5 status error', e); }
}

async function connectMT5() {
  const btn = document.getElementById('mt5-connect-btn');
  btn.disabled = true;
  btn.textContent = 'Connecting…';
  setMsg('', '');

  const body = {
    login:    document.getElementById('mt5-login').value.trim(),
    password: document.getElementById('mt5-password').value,
    server:   document.getElementById('mt5-server').value.trim(),
    symbol:   document.getElementById('mt5-symbol').value.trim() || 'US100',
  };

  try {
    const r = await fetch('/api/mt5/connect', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (d.ok) {
      setMsg('Connected successfully', 'mt5-success');
      document.getElementById('mt5-password').value = '';
      await refreshMT5();
    } else {
      setMsg('Error: ' + (d.error || 'Unknown error'), 'mt5-error');
    }
  } catch(e) {
    setMsg('Error: ' + e.message, 'mt5-error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Connect';
  }
}

async function disconnectMT5() {
  await fetch('/api/mt5/disconnect', {method: 'POST'});
  setMsg('', '');
  await refreshMT5();
}

// Allow Enter key in login form to submit
['mt5-login','mt5-password','mt5-server','mt5-symbol'].forEach(id => {
  document.getElementById(id)?.addEventListener('keydown', e => {
    if (e.key === 'Enter') connectMT5();
  });
});

// ---------------------------------------------------------------------------
// Bot status (status.json)
// ---------------------------------------------------------------------------

async function refreshStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();

    document.getElementById('last-updated').textContent = 'Updated: ' + (d.last_updated || '—');
    const badge = document.getElementById('mode-badge');
    if (d.bot_mode === 'LIVE') { badge.textContent = 'LIVE'; badge.className = 'badge live'; }
    else if (d.bot_mode)       { badge.textContent = d.bot_mode; badge.className = 'badge dry'; }
    else                       { badge.textContent = 'OFFLINE'; badge.className = 'badge stopped'; }

    const sess   = d.session || 'Closed';
    const active = d.in_kill_zone;
    document.getElementById('session-badge').innerHTML =
      `<span class="session-dot ${active ? 'active' : 'inactive'}"></span>${sess} session`;

    // Only update account cards from status.json when MT5 panel is NOT connected
    if (!mt5Connected) {
      if (d.account && d.account.balance) {
        document.getElementById('balance').textContent = '$' + fmt(d.account.balance);
        document.getElementById('equity').textContent  = '$' + fmt(d.account.equity);
        const pnl = document.getElementById('open-pnl');
        pnl.textContent = (d.account.profit >= 0 ? '+' : '') + '$' + fmt(d.account.profit);
        pnl.className = 'stat-val ' + colorVal(d.account.profit);
      }
      if (d.current_price) {
        document.getElementById('price').textContent  = fmt(d.current_price, 0);
        document.getElementById('price-sub').textContent = d.symbol || 'US100';
      }
    }

    // Open positions table (from bot status.json)
    const posWrap = document.getElementById('open-positions-wrap');
    if (d.open_positions && d.open_positions.length > 0) {
      let html = '<table><thead><tr><th>Dir</th><th>Entry</th><th>Current</th><th>SL</th><th>TP</th><th>P&amp;L</th></tr></thead><tbody>';
      for (const p of d.open_positions) {
        const dCls  = p.direction === 'bullish' ? 'dir-bull' : 'dir-bear';
        const pCls  = p.profit >= 0 ? 'win' : 'loss';
        html += `<tr>
          <td class="${dCls}">${p.direction.toUpperCase()}</td>
          <td>${fmt(p.entry)}</td>
          <td>${fmt(p.current_price)}</td>
          <td style="color:#f85149">${fmt(p.sl)}</td>
          <td style="color:#3fb950">${fmt(p.tp)}</td>
          <td class="${pCls}">${p.profit>=0?'+':''}$${fmt(p.profit)}</td>
        </tr>`;
      }
      html += '</tbody></table>';
      posWrap.innerHTML = html;
    } else {
      posWrap.innerHTML = '<p class="no-data">No open positions</p>';
    }
  } catch(e) {
    document.getElementById('mode-badge').textContent = 'OFFLINE';
    document.getElementById('mode-badge').className = 'badge stopped';
    document.getElementById('pulse').style.background = '#6e7681';
  }
}

// ---------------------------------------------------------------------------
// Trades (journal/trades.json)
// ---------------------------------------------------------------------------

async function refreshTrades() {
  try {
    const r = await fetch('/api/trades');
    const d = await r.json();

    document.getElementById('today-trades').textContent = d.today.total;
    document.getElementById('today-wl').textContent = `${d.today.wins}W / ${d.today.losses}L`;
    const todayNet = document.getElementById('today-net');
    todayNet.textContent = (d.today.net >= 0 ? '+' : '') + fmt(d.today.net);
    todayNet.className = 'stat-val ' + colorVal(d.today.net);

    document.getElementById('total-trades').textContent = d.total;
    const wr = d.total > 0 ? ((d.wins / d.total) * 100).toFixed(1) : '0.0';
    document.getElementById('total-wl').textContent = `${d.wins}W / ${d.losses}L — ${wr}% WR`;
    const totalNet = document.getElementById('total-net');
    totalNet.textContent = (d.net_pts >= 0 ? '+' : '') + fmt(d.net_pts);
    totalNet.className = 'stat-val ' + colorVal(d.net_pts);

    if (d.equity && d.equity.length > 0) {
      const labels = d.equity.map(e => e.time);
      const values = d.equity.map(e => e.value);
      const ptColors = values.map(v => v >= 0 ? '#3fb950' : '#f85149');
      if (equityChart) {
        equityChart.data.labels = labels;
        equityChart.data.datasets[0].data = values;
        equityChart.update('none');
      } else {
        const ctx = document.getElementById('equity-chart').getContext('2d');
        equityChart = new Chart(ctx, {
          type: 'line',
          data: {
            labels,
            datasets: [{
              data: values,
              borderColor: '#58a6ff',
              backgroundColor: 'rgba(88,166,255,0.08)',
              borderWidth: 2,
              pointRadius: 3,
              pointBackgroundColor: ptColors,
              fill: true,
              tension: 0.3,
            }]
          },
          options: {
            responsive: true,
            plugins: {legend: {display: false}},
            scales: {
              x: {ticks: {color:'#8b949e',maxTicksLimit:8,font:{size:10}},grid:{color:'#21262d'}},
              y: {ticks: {color:'#8b949e',font:{size:10}},grid:{color:'#21262d'}}
            }
          }
        });
      }
    }

    const wrap = document.getElementById('recent-trades-wrap');
    if (d.recent && d.recent.length > 0) {
      let html = `<table><thead><tr>
        <th>Time</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP</th>
        <th>RR</th><th>Result</th><th>PnL (pts)</th>
      </tr></thead><tbody>`;
      for (const t of d.recent) {
        const res  = t.result || 'open';
        const rCls = res === 'win' ? 'win' : res === 'loss' ? 'loss' : 'open-tag';
        const dCls = t.direction === 'bullish' ? 'dir-bull' : 'dir-bear';
        const pnl  = t.pnl_points !== null && t.pnl_points !== undefined
                     ? (t.pnl_points >= 0 ? '+' : '') + fmt(t.pnl_points) : '—';
        const pCls = t.pnl_points >= 0 ? 'win' : 'loss';
        html += `<tr>
          <td>${(t.time||'').slice(0,16)}</td>
          <td class="${dCls}">${(t.direction||'').toUpperCase()}</td>
          <td>${fmt(t.entry)}</td>
          <td style="color:#f85149">${fmt(t.sl)}</td>
          <td style="color:#3fb950">${fmt(t.tp)}</td>
          <td>${t.rr||'—'}</td>
          <td class="${rCls}">${res.toUpperCase()}</td>
          <td class="${pCls}">${pnl}</td>
        </tr>`;
      }
      html += '</tbody></table>';
      wrap.innerHTML = html;
    } else {
      wrap.innerHTML = '<p class="no-data">No trades yet — run the backtest or start the bot</p>';
    }
  } catch(e) { console.error('Trades fetch error', e); }
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

async function refresh() {
  await Promise.all([refreshMT5(), refreshStatus(), refreshTrades()]);
}

refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(TEMPLATE)


# ---------------------------------------------------------------------------
# Backtest endpoints
# ---------------------------------------------------------------------------

def _load_backtest_csv() -> list[dict]:
    if not BACKTEST_CSV.exists():
        return []
    rows = []
    with open(BACKTEST_CSV, newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def _backtest_stats(rows: list[dict]) -> dict:
    if not rows:
        return {}
    wins   = [r for r in rows if r.get("result") == "win"]
    losses = [r for r in rows if r.get("result") == "loss"]
    total  = len(rows)
    gain   = sum(float(r["pnl_points"]) for r in wins)
    loss   = abs(sum(float(r["pnl_points"]) for r in losses))
    net    = sum(float(r["pnl_points"]) for r in rows)
    pf     = round(gain / loss, 2) if loss > 0 else None

    running = peak = max_dd = 0.0
    equity  = [0.0]
    for r in rows:
        running += float(r["pnl_points"])
        equity.append(round(running, 2))
        peak   = max(peak, running)
        max_dd = max(max_dd, peak - running)

    # monthly breakdown
    monthly: dict[str, dict] = {}
    for r in rows:
        key = r["time"][:7]
        m   = monthly.setdefault(key, {"month": key, "trades": 0, "wins": 0, "losses": 0, "net": 0.0})
        m["trades"] += 1
        m["net"]    += float(r["pnl_points"])
        if r["result"] == "win":
            m["wins"] += 1
        else:
            m["losses"] += 1
    for m in monthly.values():
        m["net"]      = round(m["net"], 2)
        m["win_rate"] = round(m["wins"] / m["trades"] * 100, 1) if m["trades"] else 0.0

    # session breakdown
    sessions: dict[str, dict] = {}
    for r in rows:
        s = r.get("session", "Unknown")
        d = sessions.setdefault(s, {"session": s, "trades": 0, "wins": 0, "losses": 0, "net": 0.0})
        d["trades"] += 1
        d["net"]    += float(r["pnl_points"])
        if r["result"] == "win":
            d["wins"] += 1
        else:
            d["losses"] += 1
    for d in sessions.values():
        d["net"]      = round(d["net"], 2)
        d["win_rate"] = round(d["wins"] / d["trades"] * 100, 1) if d["trades"] else 0.0

    return {
        "total":    total,
        "wins":     len(wins),
        "losses":   len(losses),
        "win_rate": round(len(wins) / total * 100, 1),
        "net":      round(net, 2),
        "profit_factor": pf,
        "max_dd":   round(max_dd, 2),
        "equity":   equity,
        "monthly":  sorted(monthly.values(), key=lambda x: x["month"]),
        "sessions": list(sessions.values()),
        "trades":   rows[-50:][::-1],  # last 50, newest first
    }


@app.route("/api/backtest")
def api_backtest():
    rows = _load_backtest_csv()
    return jsonify(_backtest_stats(rows))


@app.route("/backtest/chart")
def backtest_chart():
    if not EQUITY_CURVE_FILE.exists():
        return ("No equity_curve.png found — run backtest.py first", 404)
    return send_file(EQUITY_CURVE_FILE, mimetype="image/png")


BACKTEST_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backtest — ICT 2022</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}
header{background:#161b22;border-bottom:1px solid #30363d;padding:14px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
header h1{font-size:1.1rem;color:#e6edf3;font-weight:600}
.back{padding:4px 14px;border-radius:12px;background:#21262d;color:#c9d1d9;font-size:.75rem;font-weight:700;text-decoration:none}
.back:hover{background:#30363d}
main{padding:20px 24px;display:flex;flex-direction:column;gap:20px}
.row{display:grid;gap:16px}
.cols-4{grid-template-columns:repeat(4,1fr)}
.cols-3{grid-template-columns:repeat(3,1fr)}
.cols-2{grid-template-columns:repeat(2,1fr)}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:18px}
.card h2{font-size:.78rem;color:#8b949e;text-transform:uppercase;letter-spacing:.06em;margin-bottom:12px}
.stat-val{font-size:1.6rem;font-weight:700;color:#e6edf3}
.stat-val.green{color:#3fb950}.stat-val.red{color:#f85149}.stat-val.blue{color:#58a6ff}
.stat-sub{font-size:.75rem;color:#8b949e;margin-top:3px}
table{width:100%;border-collapse:collapse;font-size:.82rem}
th{text-align:left;padding:8px 10px;color:#8b949e;font-weight:500;border-bottom:1px solid #30363d;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em}
td{padding:8px 10px;border-bottom:1px solid #21262d;color:#c9d1d9}
tr:last-child td{border-bottom:none}
.win{color:#3fb950;font-weight:600}.loss{color:#f85149;font-weight:600}
.no-data{color:#8b949e;font-size:.82rem;padding:12px 0}
canvas{max-height:260px}
.chart-img{width:100%;border-radius:6px;background:#0d1117}
.badge{padding:3px 10px;border-radius:12px;font-size:.72rem;font-weight:700;background:#1f6feb;color:#fff}
@media(max-width:900px){.cols-4,.cols-3{grid-template-columns:repeat(2,1fr)}}
@media(max-width:560px){.cols-4,.cols-3,.cols-2{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <h1>ICT 2022 — Backtest Results</h1>
  <span class="badge">US100</span>
  <a href="/" class="back" style="margin-left:auto">← Live Dashboard</a>
</header>
<main>

  <!-- Summary stats -->
  <div class="row cols-4">
    <div class="card"><h2>Total Trades</h2>
      <div class="stat-val blue" id="total">—</div>
      <div class="stat-sub" id="wl-sub">—</div></div>
    <div class="card"><h2>Win Rate</h2>
      <div class="stat-val" id="wr">—</div>
      <div class="stat-sub">% of trades profitable</div></div>
    <div class="card"><h2>Profit Factor</h2>
      <div class="stat-val" id="pf">—</div>
      <div class="stat-sub">Gross win / gross loss</div></div>
    <div class="card"><h2>Net Points</h2>
      <div class="stat-val" id="net">—</div>
      <div class="stat-sub" id="dd-sub">—</div></div>
  </div>

  <!-- Equity curve chart.js -->
  <div class="card">
    <h2>Equity Curve</h2>
    <canvas id="equity-chart"></canvas>
  </div>

  <!-- Equity curve image from backtest.py (richer chart) -->
  <div class="card" id="img-card" style="display:none">
    <h2>Full Backtest Chart (from backtest.py)</h2>
    <img id="equity-img" class="chart-img" src="/backtest/chart" alt="equity curve"
         onerror="document.getElementById('img-card').style.display='none'">
  </div>

  <!-- Monthly + Session -->
  <div class="row cols-2">
    <div class="card">
      <h2>Monthly Breakdown</h2>
      <div id="monthly-wrap"><p class="no-data">No data</p></div>
    </div>
    <div class="card">
      <h2>Session Breakdown</h2>
      <div id="session-wrap"><p class="no-data">No data</p></div>
    </div>
  </div>

  <!-- Recent trades table -->
  <div class="card">
    <h2>Trade Log (last 50)</h2>
    <div id="trades-wrap"><p class="no-data">No data</p></div>
  </div>

</main>
<script>
let eqChart = null;

async function load() {
  const r = await fetch('/api/backtest');
  const d = await r.json();

  if (!d.total) {
    document.querySelector('main').innerHTML =
      '<div class="card"><p class="no-data" style="padding:24px">No backtest_results.csv found — run <code>python backtest.py</code> first.</p></div>';
    return;
  }

  document.getElementById('total').textContent = d.total;
  document.getElementById('wl-sub').textContent = d.wins + 'W / ' + d.losses + 'L';

  const wrEl = document.getElementById('wr');
  wrEl.textContent = d.win_rate + '%';
  wrEl.className = 'stat-val ' + (d.win_rate >= 50 ? 'green' : 'red');

  const pfEl = document.getElementById('pf');
  pfEl.textContent = d.profit_factor ?? '∞';
  pfEl.className = 'stat-val ' + ((d.profit_factor ?? 2) >= 1.5 ? 'green' : d.profit_factor >= 1 ? '' : 'red');

  const netEl = document.getElementById('net');
  netEl.textContent = (d.net >= 0 ? '+' : '') + d.net + ' pts';
  netEl.className = 'stat-val ' + (d.net >= 0 ? 'green' : 'red');
  document.getElementById('dd-sub').textContent = 'Max DD: ' + d.max_dd + ' pts';

  // Equity curve
  if (d.equity && d.equity.length > 1) {
    const labels = d.equity.map((_, i) => i === 0 ? 'Start' : 'T' + i);
    const ptColors = d.equity.map((v, i) => i === 0 ? '#8b949e' : d.equity[i] >= d.equity[i-1] ? '#3fb950' : '#f85149');
    const ctx = document.getElementById('equity-chart').getContext('2d');
    eqChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          data: d.equity,
          borderColor: '#58a6ff',
          backgroundColor: 'rgba(88,166,255,0.08)',
          borderWidth: 2,
          pointRadius: 3,
          pointBackgroundColor: ptColors,
          fill: true,
          tension: 0.3,
        }]
      },
      options: {
        responsive: true,
        plugins: {legend: {display: false}, tooltip: {callbacks: {label: ctx => ctx.parsed.y + ' pts'}}},
        scales: {
          x: {ticks: {color:'#8b949e', maxTicksLimit:10, font:{size:10}}, grid:{color:'#21262d'}},
          y: {ticks: {color:'#8b949e', font:{size:10}}, grid:{color:'#21262d'}}
        }
      }
    });
  }

  // Show static image if it loads
  document.getElementById('img-card').style.display = 'block';

  // Monthly table
  if (d.monthly && d.monthly.length) {
    let h = '<table><thead><tr><th>Month</th><th>Trades</th><th>Wins</th><th>Losses</th><th>Win%</th><th>Net Pts</th></tr></thead><tbody>';
    for (const m of d.monthly) {
      const nc = m.net >= 0 ? 'win' : 'loss';
      h += `<tr><td>${m.month}</td><td>${m.trades}</td><td class="win">${m.wins}</td><td class="loss">${m.losses}</td><td>${m.win_rate}%</td><td class="${nc}">${m.net >= 0 ? '+' : ''}${m.net}</td></tr>`;
    }
    h += '</tbody></table>';
    document.getElementById('monthly-wrap').innerHTML = h;
  }

  // Session table
  if (d.sessions && d.sessions.length) {
    let h = '<table><thead><tr><th>Session</th><th>Trades</th><th>Wins</th><th>Losses</th><th>Win%</th><th>Net Pts</th></tr></thead><tbody>';
    for (const s of d.sessions) {
      const nc = s.net >= 0 ? 'win' : 'loss';
      h += `<tr><td>${s.session}</td><td>${s.trades}</td><td class="win">${s.wins}</td><td class="loss">${s.losses}</td><td>${s.win_rate}%</td><td class="${nc}">${s.net >= 0 ? '+' : ''}${s.net}</td></tr>`;
    }
    h += '</tbody></table>';
    document.getElementById('session-wrap').innerHTML = h;
  }

  // Trades table
  if (d.trades && d.trades.length) {
    let h = '<table><thead><tr><th>Time</th><th>Session</th><th>Result</th><th>Entry</th><th>SL</th><th>TP</th><th>PnL (pts)</th></tr></thead><tbody>';
    for (const t of d.trades) {
      const rc = t.result === 'win' ? 'win' : 'loss';
      const pc = parseFloat(t.pnl_points) >= 0 ? 'win' : 'loss';
      h += `<tr><td>${t.time}</td><td>${t.session||'—'}</td><td class="${rc}">${t.result.toUpperCase()}</td><td>${parseFloat(t.entry).toFixed(2)}</td><td style="color:#f85149">${parseFloat(t.sl).toFixed(2)}</td><td style="color:#3fb950">${parseFloat(t.tp).toFixed(2)}</td><td class="${pc}">${parseFloat(t.pnl_points)>=0?'+':''}${parseFloat(t.pnl_points).toFixed(2)}</td></tr>`;
    }
    h += '</tbody></table>';
    document.getElementById('trades-wrap').innerHTML = h;
  }
}

load();
</script>
</body>
</html>"""


@app.route("/backtest")
def backtest():
    return render_template_string(BACKTEST_TEMPLATE)


if __name__ == "__main__":
    print("Dashboard running at http://localhost:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
