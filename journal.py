"""
Trade Journal — generates annotated candlestick screenshots and a
self-contained HTML report for every trade the bot takes.

Output:
  journal/
  ├── trades.json               persistent trade log
  ├── journal.html              browse-ready HTML report
  └── screenshots/
      └── 20240101_0830_BULLISH_12345678.png
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")   # headless — works on Kali / servers without a display
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd

log = logging.getLogger(__name__)

JOURNAL_DIR     = Path("journal")
SCREENSHOTS_DIR = JOURNAL_DIR / "screenshots"
TRADES_JSON     = JOURNAL_DIR / "trades.json"
JOURNAL_HTML    = JOURNAL_DIR / "journal.html"
CANDLES_SHOWN   = 80


class TradeJournal:

    def __init__(self):
        JOURNAL_DIR.mkdir(exist_ok=True)
        SCREENSHOTS_DIR.mkdir(exist_ok=True)
        self.trades: dict = self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_entry(self, position_id, df_15m: pd.DataFrame,
                  mss: dict, fvg: dict,
                  entry: float, sl: float, tp: float,
                  direction: str, signal_time: datetime,
                  features: dict = None):
        key   = str(position_id)
        sl_d  = abs(entry - sl)
        trade = {
            "id":         position_id,
            "time":       signal_time.strftime("%Y-%m-%d %H:%M UTC"),
            "direction":  direction,
            "entry":      round(entry, 2),
            "sl":         round(sl, 2),
            "tp":         round(tp, 2),
            "rr":         round(abs(tp - entry) / sl_d, 2) if sl_d > 0 else 0,
            "mss":        {k: (round(v, 2) if isinstance(v, float) else v)
                           for k, v in mss.items()},
            "fvg":        {k: round(v, 4) for k, v in fvg.items()
                           if isinstance(v, float)},
            "features":   features or {},
            "result":     None,
            "pnl_points": None,
            "exit_time":  None,
            "screenshot": None,
        }

        png = self._generate_chart(trade, df_15m)
        trade["screenshot"] = png.name if png else None

        self.trades[key] = trade
        self._save()
        self._build_html()
        log.info("Journal: entry logged | pos %s | %s @ %.2f", key, direction, entry)

    def log_exit(self, position_id, result: str,
                 pnl_points: float, exit_time: datetime = None):
        key = str(position_id)
        if key not in self.trades:
            return
        self.trades[key]["result"]     = result
        self.trades[key]["pnl_points"] = round(pnl_points, 2)
        self.trades[key]["exit_time"]  = (
            exit_time.strftime("%Y-%m-%d %H:%M UTC") if exit_time else None
        )
        self._save()
        self._build_html()
        log.info("Journal: exit logged | pos %s | %s | %+.1f pts",
                 key, result, pnl_points)

    # ------------------------------------------------------------------
    # Chart generation
    # ------------------------------------------------------------------

    def _generate_chart(self, trade: dict, df_15m: pd.DataFrame) -> Path | None:
        try:
            df = df_15m.tail(CANDLES_SHOWN).reset_index(drop=True)
            if df.empty:
                return None

            entry     = trade["entry"]
            sl        = trade["sl"]
            tp        = trade["tp"]
            direction = trade["direction"]
            result    = trade["result"]
            fvg       = trade["fvg"]

            fig, ax = plt.subplots(figsize=(14, 7))
            fig.patch.set_facecolor("#0d1117")
            ax.set_facecolor("#0d1117")
            ax.tick_params(colors="#c9d1d9")
            ax.spines[:].set_color("#30363d")

            # Candlesticks
            self._draw_candles(ax, df)

            # FVG zone
            ax.axhspan(fvg["bottom"], fvg["top"],
                       alpha=0.18, color="#e3b341", label="FVG")

            # Trade levels
            n = len(df)
            ax.axhline(entry, color="#58a6ff", lw=1.3, ls="--",
                       label=f"Entry {entry:.0f}")
            ax.axhline(sl,    color="#f85149", lw=1.3, ls=":",
                       label=f"SL {sl:.0f}")
            ax.axhline(tp,    color="#3fb950", lw=1.3, ls="-.",
                       label=f"TP {tp:.0f}")

            # Price labels on right edge
            for price, color, label in [(entry, "#58a6ff", f"E {entry:.0f}"),
                                         (sl,    "#f85149", f"SL {sl:.0f}"),
                                         (tp,    "#3fb950", f"TP {tp:.0f}")]:
                ax.text(n + 0.4, price, f" {label}",
                        color=color, va="center", fontsize=8)

            # MSS marker (last candle in window = structure break candle)
            ax.axvline(n - 1, color="#e3b341", lw=0.8, ls="--", alpha=0.6,
                       label="MSS")

            # Result badge
            if result:
                badge_color = "#3fb950" if result == "win" else "#f85149"
                symbol = "✓ WIN" if result == "win" else "✗ LOSS"
                ax.text(0.02, 0.97,
                        f"{symbol}  {trade['pnl_points']:+.1f} pts",
                        transform=ax.transAxes,
                        color=badge_color, fontsize=13, fontweight="bold",
                        va="top",
                        bbox=dict(boxstyle="round,pad=0.3",
                                  facecolor="#161b22", edgecolor=badge_color))

            # Info box
            session_map = {0: "London", 1: "NY", 2: "Other"}
            session = session_map.get(trade["features"].get("session", 2), "—")
            info = (f"Dir     : {direction.upper()}\n"
                    f"Session : {session}\n"
                    f"Entry   : {entry:.2f}\n"
                    f"SL      : {sl:.2f}\n"
                    f"TP      : {tp:.2f}\n"
                    f"RR      : 1 : {trade.get('rr', 0):.1f}")
            ax.text(0.02, 0.72, info,
                    transform=ax.transAxes,
                    color="#8b949e", fontsize=8, va="top",
                    fontfamily="monospace",
                    bbox=dict(boxstyle="round,pad=0.4",
                              facecolor="#161b22", edgecolor="#30363d", alpha=0.9))

            # X-axis time labels
            step  = max(1, n // 10)
            ticks = list(range(0, n, step))
            ax.set_xticks(ticks)
            ax.set_xticklabels(
                [df.iloc[i]["time"].strftime("%m/%d %H:%M") for i in ticks],
                rotation=30, ha="right", fontsize=7, color="#c9d1d9",
            )
            ax.set_xlim(-1, n + 5)

            ax.set_title(
                f"ICT 2022 — US100 | {trade['time']} | {direction.upper()}",
                color="#e6edf3", fontsize=11, pad=8,
            )
            ax.legend(loc="upper right", facecolor="#161b22",
                      edgecolor="#30363d", labelcolor="#c9d1d9", fontsize=8)

            ts   = datetime.strptime(trade["time"], "%Y-%m-%d %H:%M UTC")
            name = (f"{ts.strftime('%Y%m%d_%H%M')}_"
                    f"{direction.upper()}_{trade['id']}.png")
            path = SCREENSHOTS_DIR / name
            plt.tight_layout()
            plt.savefig(path, dpi=130, bbox_inches="tight",
                        facecolor=fig.get_facecolor())
            plt.close()
            return path

        except Exception as exc:
            log.error("Chart generation failed: %s", exc, exc_info=True)
            plt.close("all")
            return None

    @staticmethod
    def _draw_candles(ax, df: pd.DataFrame):
        bull, bear = "#3fb950", "#f85149"
        for i, row in df.iterrows():
            color = bull if row["close"] >= row["open"] else bear
            lo = min(row["open"], row["close"])
            hi = max(row["open"], row["close"])
            ax.add_patch(mpatches.Rectangle(
                (i - 0.35, lo), 0.7, max(hi - lo, 0.01),
                color=color, zorder=3,
            ))
            ax.plot([i, i], [row["low"],  lo], color=color, lw=0.7, zorder=2)
            ax.plot([i, i], [hi, row["high"]], color=color, lw=0.7, zorder=2)
        margin = (df["high"].max() - df["low"].min()) * 0.05
        ax.set_ylim(df["low"].min() - margin, df["high"].max() + margin)

    # ------------------------------------------------------------------
    # HTML report
    # ------------------------------------------------------------------

    def _build_html(self):
        trades   = list(self.trades.values())
        total    = len(trades)
        wins     = sum(1 for t in trades if t["result"] == "win")
        losses   = sum(1 for t in trades if t["result"] == "loss")
        open_    = sum(1 for t in trades if t["result"] is None)
        win_rate = f"{wins / total * 100:.1f}%" if total else "—"
        net_pts  = sum(t["pnl_points"] or 0 for t in trades)
        net_col  = "#3fb950" if net_pts >= 0 else "#f85149"

        cards = ""
        session_map = {0: "London", 1: "NY", 2: "Other"}

        for t in sorted(trades, key=lambda x: x["time"], reverse=True):
            result    = t["result"] or "open"
            col_map   = {"win": "#3fb950", "loss": "#f85149", "open": "#58a6ff"}
            col       = col_map.get(result, "#8b949e")
            pnl_str   = (f"{t['pnl_points']:+.1f} pts"
                         if t["pnl_points"] is not None else "—")
            session   = session_map.get(t["features"].get("session", 2), "—")
            exit_row  = (f'<div><label>Exit</label>'
                         f'<span>{t["exit_time"]}</span></div>'
                         if t["exit_time"] else "")
            img_tag   = (f'<img src="screenshots/{t["screenshot"]}" '
                         f'style="width:100%;border-radius:6px;margin-top:12px;'
                         f'border:1px solid #30363d">'
                         if t["screenshot"] else
                         '<p style="color:#8b949e;font-size:.8rem;margin-top:10px">'
                         'No screenshot</p>')

            cards += f"""
            <div class="card" style="border-left:3px solid {col}">
              <div class="card-header">
                <span class="badge" style="background:{col}">{result.upper()}</span>
                <span class="time">{t["time"]}</span>
                <span class="dir {t["direction"]}">{t["direction"].upper()}</span>
              </div>
              <div class="stats">
                <div><label>Entry</label><span>{t["entry"]:.2f}</span></div>
                <div><label>SL</label>
                     <span style="color:#f85149">{t["sl"]:.2f}</span></div>
                <div><label>TP</label>
                     <span style="color:#3fb950">{t["tp"]:.2f}</span></div>
                <div><label>RR</label><span>1 : {t.get("rr",0):.1f}</span></div>
                <div><label>Session</label><span>{session}</span></div>
                <div><label>PnL</label>
                     <span style="color:{col}">{pnl_str}</span></div>
                {exit_row}
              </div>
              {img_tag}
            </div>"""

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ICT 2022 Trade Journal — US100</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#c9d1d9;
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      padding:28px 24px}}
h1{{color:#e6edf3;font-size:1.45rem;margin-bottom:4px}}
.sub{{color:#8b949e;font-size:.82rem;margin-bottom:24px}}
.summary{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:28px}}
.stat{{background:#161b22;border:1px solid #30363d;border-radius:8px;
       padding:14px 20px;min-width:110px}}
.stat label{{font-size:.68rem;color:#8b949e;display:block;margin-bottom:4px;
             text-transform:uppercase;letter-spacing:.06em}}
.stat span{{font-size:1.35rem;font-weight:600;color:#e6edf3}}
.grid{{display:grid;
       grid-template-columns:repeat(auto-fill,minmax(500px,1fr));
       gap:20px}}
.card{{background:#161b22;border:1px solid #30363d;
       border-radius:10px;padding:16px}}
.card-header{{display:flex;align-items:center;gap:10px;margin-bottom:14px}}
.badge{{padding:2px 10px;border-radius:12px;font-size:.7rem;
        font-weight:700;color:#0d1117}}
.time{{font-size:.8rem;color:#8b949e}}
.dir{{font-size:.82rem;font-weight:600;margin-left:auto}}
.dir.bullish{{color:#3fb950}}.dir.bearish{{color:#f85149}}
.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px 14px}}
.stats div{{display:flex;flex-direction:column}}
.stats label{{font-size:.65rem;color:#8b949e;text-transform:uppercase;
              letter-spacing:.05em}}
.stats span{{font-size:.88rem;color:#e6edf3;font-weight:500;margin-top:2px}}
@media(max-width:560px){{
  .grid{{grid-template-columns:1fr}}
  .summary{{flex-direction:column}}
}}
</style>
</head>
<body>
<h1>ICT 2022 Trade Journal — US100</h1>
<p class="sub">Updated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</p>
<div class="summary">
  <div class="stat"><label>Total</label><span>{total}</span></div>
  <div class="stat"><label>Wins</label>
       <span style="color:#3fb950">{wins}</span></div>
  <div class="stat"><label>Losses</label>
       <span style="color:#f85149">{losses}</span></div>
  <div class="stat"><label>Open</label>
       <span style="color:#58a6ff">{open_}</span></div>
  <div class="stat"><label>Win Rate</label><span>{win_rate}</span></div>
  <div class="stat"><label>Net Points</label>
       <span style="color:{net_col}">{net_pts:+.1f}</span></div>
</div>
<div class="grid">{cards}</div>
</body>
</html>"""

        with open(JOURNAL_HTML, "w") as f:
            f.write(html)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if TRADES_JSON.exists():
            with open(TRADES_JSON) as f:
                return json.load(f)
        return {}

    def _save(self):
        with open(TRADES_JSON, "w") as f:
            json.dump(self.trades, f, indent=2, default=str)
