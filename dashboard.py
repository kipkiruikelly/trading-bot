"""
Live monitoring dashboard for the ICT 2022 trading bot.
Runs independently — no MT5 required on the dashboard machine.

Usage:
    python dashboard.py
    Then open http://localhost:5000 in any browser.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

STATUS_FILE  = Path("status.json")
TRADES_FILE  = Path("journal/trades.json")
PENDING_FILE = Path("pending_trades.json")


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
# API endpoints
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
@media(max-width:900px){.row.cols-4,.row.cols-3{grid-template-columns:repeat(2,1fr)}}
@media(max-width:560px){.row.cols-4,.row.cols-3,.row.cols-2{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <div class="pulse" id="pulse"></div>
  <h1>ICT 2022 — US100 Dashboard</h1>
  <span class="badge dry" id="mode-badge">—</span>
  <span id="session-badge" style="font-size:.8rem;color:#8b949e">—</span>
  <span class="updated" id="last-updated">—</span>
</header>

<main>
  <!-- Account Stats -->
  <div class="row cols-4">
    <div class="card"><h2>Balance</h2>
      <div class="stat-val" id="balance">—</div>
      <div class="stat-sub">Account balance</div></div>
    <div class="card"><h2>Equity</h2>
      <div class="stat-val" id="equity">—</div>
      <div class="stat-sub">Current equity</div></div>
    <div class="card"><h2>Open P&L</h2>
      <div class="stat-val" id="open-pnl">—</div>
      <div class="stat-sub">Floating profit</div></div>
    <div class="card"><h2>US100 Price</h2>
      <div class="stat-val blue" id="price">—</div>
      <div class="stat-sub" id="price-sub">—</div></div>
  </div>

  <!-- Today + All Time Stats -->
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

function fmt(n, decimals=2) {
  if (n === null || n === undefined) return '—';
  return Number(n).toLocaleString(undefined, {minimumFractionDigits: decimals, maximumFractionDigits: decimals});
}

function colorVal(n) {
  if (n === null || n === undefined) return '';
  return n >= 0 ? 'green' : 'red';
}

async function refreshStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();

    // Header
    document.getElementById('last-updated').textContent = 'Updated: ' + (d.last_updated || '—');
    const badge = document.getElementById('mode-badge');
    if (d.bot_mode === 'LIVE') { badge.textContent = 'LIVE'; badge.className = 'badge live'; }
    else if (d.bot_mode) { badge.textContent = d.bot_mode; badge.className = 'badge dry'; }
    else { badge.textContent = 'OFFLINE'; badge.className = 'badge stopped'; }

    const sess = d.session || 'Closed';
    const active = d.in_kill_zone;
    document.getElementById('session-badge').innerHTML =
      `<span class="session-dot ${active ? 'active' : 'inactive'}"></span>${sess} session`;

    // Account
    if (d.account && d.account.balance) {
      document.getElementById('balance').textContent = '$' + fmt(d.account.balance);
      const eq = document.getElementById('equity');
      eq.textContent = '$' + fmt(d.account.equity);
      const pnl = document.getElementById('open-pnl');
      pnl.textContent = (d.account.profit >= 0 ? '+' : '') + '$' + fmt(d.account.profit);
      pnl.className = 'stat-val ' + colorVal(d.account.profit);
    }

    if (d.current_price) {
      document.getElementById('price').textContent = fmt(d.current_price);
      document.getElementById('price-sub').textContent = d.symbol || 'US100';
    }

    // Open positions table
    const posWrap = document.getElementById('open-positions-wrap');
    if (d.open_positions && d.open_positions.length > 0) {
      let html = '<table><thead><tr><th>Dir</th><th>Entry</th><th>Current</th><th>SL</th><th>TP</th><th>P&L</th></tr></thead><tbody>';
      for (const p of d.open_positions) {
        const dirClass = p.direction === 'bullish' ? 'dir-bull' : 'dir-bear';
        const pnlClass = p.profit >= 0 ? 'win' : 'loss';
        html += `<tr>
          <td class="${dirClass}">${p.direction.toUpperCase()}</td>
          <td>${fmt(p.entry)}</td>
          <td>${fmt(p.current_price)}</td>
          <td style="color:#f85149">${fmt(p.sl)}</td>
          <td style="color:#3fb950">${fmt(p.tp)}</td>
          <td class="${pnlClass}">${p.profit >= 0 ? '+' : ''}$${fmt(p.profit)}</td>
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

async function refreshTrades() {
  try {
    const r = await fetch('/api/trades');
    const d = await r.json();

    // Today stats
    document.getElementById('today-trades').textContent = d.today.total;
    document.getElementById('today-wl').textContent = `${d.today.wins}W / ${d.today.losses}L`;
    const todayNet = document.getElementById('today-net');
    todayNet.textContent = (d.today.net >= 0 ? '+' : '') + fmt(d.today.net);
    todayNet.className = 'stat-val ' + colorVal(d.today.net);

    // All-time stats
    document.getElementById('total-trades').textContent = d.total;
    const wr = d.total > 0 ? ((d.wins / d.total) * 100).toFixed(1) : '0.0';
    document.getElementById('total-wl').textContent = `${d.wins}W / ${d.losses}L — ${wr}% WR`;
    const totalNet = document.getElementById('total-net');
    totalNet.textContent = (d.net_pts >= 0 ? '+' : '') + fmt(d.net_pts);
    totalNet.className = 'stat-val ' + colorVal(d.net_pts);

    // Equity curve
    if (d.equity && d.equity.length > 0) {
      const labels = d.equity.map(e => e.time);
      const values = d.equity.map(e => e.value);
      const colors = values.map(v => v >= 0 ? '#3fb950' : '#f85149');

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
              pointBackgroundColor: colors,
              fill: true,
              tension: 0.3,
            }]
          },
          options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
              x: { ticks: { color: '#8b949e', maxTicksLimit: 8, font: { size: 10 } },
                   grid: { color: '#21262d' } },
              y: { ticks: { color: '#8b949e', font: { size: 10 } },
                   grid: { color: '#21262d' } }
            }
          }
        });
      }
    }

    // Recent trades table
    const wrap = document.getElementById('recent-trades-wrap');
    if (d.recent && d.recent.length > 0) {
      let html = `<table><thead><tr>
        <th>Time</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP</th>
        <th>RR</th><th>Result</th><th>PnL (pts)</th>
      </tr></thead><tbody>`;
      for (const t of d.recent) {
        const res   = t.result || 'open';
        const rCls  = res === 'win' ? 'win' : res === 'loss' ? 'loss' : 'open-tag';
        const dCls  = t.direction === 'bullish' ? 'dir-bull' : 'dir-bear';
        const pnl   = t.pnl_points !== null && t.pnl_points !== undefined
                      ? (t.pnl_points >= 0 ? '+' : '') + fmt(t.pnl_points) : '—';
        const pCls  = t.pnl_points >= 0 ? 'win' : 'loss';
        html += `<tr>
          <td>${(t.time || '').slice(0, 16)}</td>
          <td class="${dCls}">${(t.direction || '').toUpperCase()}</td>
          <td>${fmt(t.entry)}</td>
          <td style="color:#f85149">${fmt(t.sl)}</td>
          <td style="color:#3fb950">${fmt(t.tp)}</td>
          <td>${t.rr || '—'}</td>
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

async function refresh() {
  await Promise.all([refreshStatus(), refreshTrades()]);
}

refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(TEMPLATE)


if __name__ == "__main__":
    print("Dashboard running at http://localhost:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
